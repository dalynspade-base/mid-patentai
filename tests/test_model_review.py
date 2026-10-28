"""模型复核的离线契约回归；模拟响应不代表真实模型的专利语义准确率。"""
import hashlib
import json
import unittest
from dataclasses import asdict, replace
from unittest.mock import Mock

from modules.google_patent_documents import manual_google_document
from modules.llm_client import LLMClient
from modules.novelty_models import Feature, review_input_hash, stamp_review
from modules.novelty_report import to_markdown
from skills.report_delivery_skill import build_report as report_payload
from modules.novelty_review import MODEL_REVIEW_PROMPT_VERSION, model_review_documents
from modules.novelty_rules import assess_novelty


def review_response(document_id="US123", quote="压力传感器", status="disclosed"):
    """明确标记为模拟的服务响应；同时用于界面测试，不调用实际服务。"""
    return {"document_id": document_id,
        "rows": [{"feature_id": "F1", "status": status, "section_id": "manual-1",
                  "quote": quote, "scheme_id": "S1" if status == "disclosed" else "",
                  "basis": "模拟复核：核对本句的技术含义、否定和完整限定"}],
        "same_scheme_confirmed": True, "enabling_disclosure_confirmed": True,
        "same_scheme_basis": "模拟复核：本段仅描述一个装置", "scheme_section_ids": ["manual-1"],
        "relevance": "related", "relevance_basis": "模拟复核：同为压力测量装置",
        "relevance_section_ids": ["manual-1"], "context_complete": True,
        "completeness_basis": "模拟复核：输入相关上下文完整", "reviewed_section_ids": ["manual-1"]}


class ModelReviewTests(unittest.TestCase):
    def setUp(self):
        self.features = [Feature("F1", "压力传感器")]
        self.doc = manual_google_document("https://patents.google.com/patent/US123/en", "模拟快照",
                                         "2023-01-01", "装置包括压力传感器。", complete=True)
        self.client = LLMClient("test-key", "https://model.example/v1", "test-model")
        self.client.complete = Mock(return_value=json.dumps(review_response(), ensure_ascii=False))

    def run_review(self, data=None, **kwargs):
        if data is not None:
            self.client.complete.return_value = json.dumps(data, ensure_ascii=False)
        return model_review_documents(self.features, [kwargs.pop("document", self.doc)], "2024-01-01",
                                      self.client, kwargs.pop("confirmed", True), **kwargs)[0]

    def decide(self, review, document=None):
        return assess_novelty(self.features, [document or self.doc], [review], "2024-01-01")

    def test_valid_review_records_model_provenance_without_modifying_source(self):
        before = self.doc.to_dict()
        review = self.run_review()
        self.assertEqual(review.review_method, "model")
        self.assertEqual(review.review_model, "test-model")
        self.assertEqual(review.review_prompt_version, MODEL_REVIEW_PROMPT_VERSION)
        self.assertTrue(review.reviewed_at)
        self.assertTrue(review.input_hash)
        self.assertTrue(all(row.origin == "model" for row in review.rows))
        self.assertEqual(self.doc.to_dict(), before)
        decision = self.decide(review)
        self.assertEqual(decision.label, "potential_novelty_risk")
        self.assertTrue(any("大模型" in warning for warning in decision.warnings))

    def test_no_consent_prevents_any_request(self):
        with self.assertRaises(PermissionError):
            self.run_review(confirmed=False)
        self.client.complete.assert_not_called()

    def test_candidate_cannot_be_stamped_as_completed_review(self):
        from modules.novelty_matching import SynonymLexicon
        from modules.precheck_graph import suggest_rows
        candidate = suggest_rows(self.features, [self.doc], SynonymLexicon())[0]
        self.assertEqual(candidate.review_method, "candidate")
        with self.assertRaises(ValueError):
            stamp_review(replace(candidate, reviewed=True), self.features, self.doc, "2024-01-01")

    def test_unknown_is_not_converted_to_absence(self):
        review = self.run_review(review_response(status="unknown"))
        self.assertEqual(self.decide(review).label, "insufficient_evidence")

    def test_not_found_is_limited_to_reviewed_documents(self):
        review = self.run_review(review_response(status="not_found"))
        self.assertEqual(self.decide(review).label, "no_complete_disclosure_found")

    def test_incomplete_source_and_incomplete_model_context_cannot_clear_risk(self):
        incomplete = replace(self.doc, complete=False)
        review = self.run_review(document=incomplete)
        self.assertEqual(self.decide(review, incomplete).label, "insufficient_evidence")
        response = review_response()
        response["context_complete"] = False
        self.assertEqual(self.decide(self.run_review(response)).label, "insufficient_evidence")

    def test_parsed_structure_requires_additional_explicit_model_context_assessment(self):
        from modules.google_patent_documents import parse_google_page
        doc = parse_google_page(self.doc.url, '<meta itemprop="publicationDate" content="2023-01-01">'
            '<div class="claim" num="1">压力传感器</div>'
            '<div itemprop="description"><div class="description-paragraph">压力传感器</div></div>')
        response = review_response()
        response["rows"][0]["section_id"] = doc.sections[0].id
        for key in ("scheme_section_ids", "relevance_section_ids"):
            response[key] = [doc.sections[0].id]
        response["reviewed_section_ids"] = [s.id for s in doc.sections]
        review = self.run_review(response, document=doc)
        self.assertFalse(doc.complete)
        self.assertEqual(self.decide(review, doc).label, "potential_novelty_risk")
        response["context_complete"] = False
        self.assertEqual(self.decide(self.run_review(response, document=doc), doc).label, "insufficient_evidence")

    def test_malformed_response_missing_or_duplicate_features_and_locations_are_rejected(self):
        variants = []
        for key, value in [("document_id", "OTHER"), ("rows", []), ("rows", {}),
                           ("reviewed_section_ids", []), ("reviewed_section_ids", ["manual-1", "manual-1"]),
                           ("relevance_section_ids", ["invented"]), ("same_scheme_confirmed", "true"),
                           ("context_complete", 1), ("completeness_basis", ""), ("relevance", "yes")]:
            data = review_response()
            data[key] = value
            variants.append(data)
        data = review_response()
        data["rows"] *= 2
        variants.append(data)
        for data in variants:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.run_review(data)

    def test_invalid_json_and_non_object_response_are_rejected(self):
        for text in ("not json", "[]"):
            self.client.complete.return_value = text
            with self.assertRaises(ValueError):
                self.run_review()

    def test_fabricated_quotes_are_rejected_for_every_status(self):
        for status in ("disclosed", "unknown", "not_found"):
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.run_review(review_response(quote="invented evidence", status=status))

    def test_missing_explanation_or_scheme_is_rejected(self):
        for field in ("basis", "quote", "scheme_id"):
            data = review_response()
            data["rows"][0][field] = ""
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.run_review(data)

    def test_synonym_negation_numbers_and_relations_are_sent_unchanged(self):
        self.features = [Feature("F1", "压力传感器不串联控制器；阈值为10 kPa")]
        doc = replace(self.doc, sections=(replace(self.doc.sections[0],
            text="The pressure transducer is not in series with the controller; threshold is 10 kPa."),))
        self.run_review(review_response(quote=doc.sections[0].text, status="unknown"), document=doc)
        sent = json.loads(self.client.complete.call_args.args[1])
        self.assertEqual(sent["features"][0]["text"], self.features[0].text)
        self.assertEqual(sent["document"]["sections"][0]["text"], doc.sections[0].text)

    def test_different_implementation_schemes_are_not_silently_merged(self):
        response = review_response()
        response["same_scheme_confirmed"] = False
        review = self.run_review(response)
        self.assertEqual(self.decide(review).label, "insufficient_evidence")

    def test_missing_metadata_and_mutated_inputs_invalidate_completed_review(self):
        review = self.run_review()
        for changes in ({"review_model": "other"}, {"review_prompt_version": "other"},
                        {"context_complete": False}, {"reviewed_section_ids": ()}):
            with self.subTest(changes=changes):
                self.assertEqual(self.decide(replace(review, **changes)).label, "insufficient_evidence")
        missing = stamp_review(replace(review, review_model=""), self.features, self.doc, "2024-01-01")
        self.assertEqual(self.decide(missing).label, "insufficient_evidence")

    def test_response_cannot_inject_human_identity_or_timestamp(self):
        data = review_response() | {"reviewer": "human-expert", "review_method": "human", "reviewed_at": "fake"}
        data["rows"][0]["origin"] = "human"
        review = self.run_review(data)
        self.assertEqual(review.reviewer, "模型：test-model")
        self.assertEqual(review.review_method, "model")
        self.assertEqual(review.rows[0].origin, "model")
        self.assertNotEqual(review.reviewed_at, "fake")

    def test_each_document_has_an_isolated_request_and_batch_failure_has_no_result(self):
        other = replace(self.doc, id="US456", url="https://patents.google.com/patent/US456/en")
        self.client.complete.side_effect = [json.dumps(review_response()), json.dumps(review_response("US456"))]
        reviews = model_review_documents(self.features, [self.doc, other], "2024-01-01", self.client, True)
        self.assertEqual([r.document_id for r in reviews], ["US123", "US456"])
        for call, doc_id in zip(self.client.complete.call_args_list, ("US123", "US456")):
            payload = json.loads(call.args[1])
            self.assertEqual(payload["document"]["id"], doc_id)
            self.assertNotIn("documents", payload)
        self.client.complete.side_effect = [json.dumps(review_response()), RuntimeError("service unavailable")]
        with self.assertRaises(RuntimeError):
            model_review_documents(self.features, [self.doc, other], "2024-01-01", self.client, True)

    def test_entire_batch_is_checked_for_size_and_source_errors_before_sending(self):
        huge = replace(self.doc, id="US456", sections=(replace(self.doc.sections[0], text="x" * 50001),))
        for doc in (huge, replace(self.doc, sections=()), replace(self.doc, error="failed")):
            with self.subTest(doc=doc.id), self.assertRaises(ValueError):
                model_review_documents(self.features, [self.doc, doc], "2024-01-01", self.client, True)
        self.client.complete.assert_not_called()

    def test_report_marks_model_and_does_not_export_url_secrets(self):
        self.client.url = "https://user:password@model.example/v1/chat/completions?token=private"
        review = self.run_review()
        payload = report_payload({"features": self.features, "documents": [self.doc], "reviews": [review],
            "decision": self.decide(review), "trace": []}, "模拟输入", "2024-01-01", [])
        markdown = to_markdown(payload)
        self.assertEqual(payload["report_schema_version"], 3)
        self.assertIn("大模型逐项复核（非人工确认）", markdown)
        self.assertIn("test-model", markdown)
        for secret in ("password", "token=private", "test-key"):
            self.assertNotIn(secret, json.dumps(payload))

    def test_historical_human_review_hash_is_preserved(self):
        from tests.test_evidence_review import EvidenceReviewTests
        features, document, review = EvidenceReviewTests().valid_inputs()
        data = asdict(review)
        for key in ("input_hash", "review_method", "review_model", "review_endpoint", "review_prompt_version",
                    "context_complete", "completeness_basis", "reviewed_section_ids", "review_context_hash"):
            data.pop(key)
        raw = json.dumps({"features": [asdict(f) for f in features], "cutoff": "2024-01-01",
            "snapshot_hash": document.snapshot_hash, "review": data}, ensure_ascii=False, sort_keys=True)
        self.assertEqual(review_input_hash(features, document, "2024-01-01", review),
                         hashlib.sha256(raw.encode("utf-8")).hexdigest())


if __name__ == "__main__":
    unittest.main()
