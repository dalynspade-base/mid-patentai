"""模型为主的证据对照契约；使用模拟响应，不评估真实模型准确率。"""
import json
import unittest
from dataclasses import asdict, replace
from unittest.mock import Mock

from modules.evidence_alignment import locate_evidence
from modules.google_patent_documents import manual_google_document
from modules.llm_client import LLMClient
from modules.novelty_models import Feature
from modules.novelty_report import to_markdown
from modules.patent_tools import build_report as report_payload
from modules.novelty_review import model_review_documents, model_review_payload
from modules.novelty_rules import assess_novelty
from modules.harness import NoveltyHarness
from modules.harness import model_suggest_rows
from tests.test_model_review import review_response


class EvidenceAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.features = [Feature("F1", "压力传感器")]
        self.doc = manual_google_document("https://patents.google.com/patent/US123/en", "模拟原文",
            "2023-01-01", "装置不包括压力传感器。\n\n另一方案采用压阻桥感测流体压力。", complete=True)
        self.client = LLMClient("test-key", "https://model.example/v1", "test-model")
        self.response = review_response(quote="压阻桥感测流体压力")
        self.response["rows"][0].update(section_id="manual-2", scheme_id="S2",
            basis="模拟语义对照：压阻桥在该方案中承担压力感测功能，第一段的否定不能用于支持。")
        self.response["scheme_section_ids"] = ["manual-2"]
        self.response["reviewed_section_ids"] = ["manual-1", "manual-2"]
        self.client.complete = Mock(return_value=json.dumps(self.response, ensure_ascii=False))

    def run_review(self, **kwargs):
        return model_review_documents(self.features, [self.doc], "2024-01-01", self.client, True, **kwargs)[0]

    def test_each_document_gets_hints_without_auto_disclosure_or_scheme_assignment(self):
        docs = [replace(self.doc, id=f"US{number}", url=f"https://patents.google.com/patent/US{number}")
                for number in range(5)]
        hits, candidates = locate_evidence(self.features, docs)
        self.assertEqual({hit["document_id"] for hit in hits}, {doc.id for doc in docs})
        for candidate in candidates:
            self.assertFalse(candidate.reviewed)
            self.assertEqual(candidate.rows[0].status, "unknown")
            self.assertEqual(candidate.rows[0].scheme_id, "")

    def test_model_can_correct_negated_candidate_and_quote_outside_retrieval_hints(self):
        hits, candidates = locate_evidence(self.features, [self.doc])
        misleading = [hit for hit in hits if hit["section_id"] == "manual-1"]
        self.assertTrue(misleading)
        review = self.run_review(candidates=candidates, evidence_hits=misleading)
        self.assertEqual(review.rows[0].section_id, "manual-2")
        self.assertEqual(review.rows[0].origin, "model")
        self.assertEqual(assess_novelty(self.features, [self.doc], [review], "2024-01-01").label,
                         "potential_novelty_risk")
        payload = json.loads(self.client.complete.call_args.args[1])
        self.assertEqual(len(payload["document"]["sections"]), 2)
        self.assertEqual({hint["section_id"] for hint in payload["retrieval_hints"]}, {"manual-1"})

    def test_no_local_hits_still_sends_all_original_text_for_semantic_alignment(self):
        review = self.run_review(evidence_hits=[])
        self.assertEqual(review.rows[0].status, "disclosed")
        payload = json.loads(self.client.complete.call_args.args[1])
        self.assertEqual(payload["retrieval_hints"], [])
        self.assertEqual(payload["document"]["sections"], [asdict(section) for section in self.doc.sections])
        self.client.complete.assert_called_once()

    def test_preview_and_sent_payload_are_identical(self):
        hits, candidates = locate_evidence(self.features, [self.doc])
        preview = model_review_payload(self.features, self.doc, "2024-01-01", candidates[0], hits)
        self.run_review(candidates=candidates, evidence_hits=hits)
        self.assertEqual(json.loads(self.client.complete.call_args.args[1]), json.loads(json.dumps(preview)))
        self.assertTrue(all("text" not in hint for hint in preview["retrieval_hints"]))

    def test_stale_or_fabricated_hints_are_rejected_before_any_model_request(self):
        hits, _ = locate_evidence(self.features, [self.doc])
        for change in ({"feature_id": "F9"}, {"section_id": "invented"},
                       {"snapshot_hash": "stale"}, {"text": "fabricated"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.run_review(evidence_hits=[hits[0] | change])
        self.client.complete.assert_not_called()

    def test_selected_document_receives_only_its_own_hints_and_original(self):
        other = replace(self.doc, id="US456", url="https://patents.google.com/patent/US456/en")
        hits, candidates = locate_evidence(self.features, [self.doc, other])
        self.run_review(candidates=candidates, evidence_hits=hits)
        payload = json.loads(self.client.complete.call_args.args[1])
        self.assertEqual({hint["document_id"] for hint in payload["retrieval_hints"]}, {self.doc.id})
        self.assertEqual(payload["document"]["id"], self.doc.id)

    def test_legacy_suggestion_uses_shared_model_alignment_without_becoming_final_review(self):
        self.client.complete.return_value = json.dumps({"reviews": [{"document_id": self.doc.id,
                                                                   "rows": self.response["rows"]}]})
        candidate = model_suggest_rows(self.features, [self.doc], self.client, True)[0]
        payload = json.loads(self.client.complete.call_args.args[1])
        self.assertEqual(payload["evidence_alignment"]["primary"], "model_semantics")
        self.assertTrue(payload["retrieval_hints"])
        self.assertFalse(candidate.reviewed)
        self.assertEqual(candidate.review_method, "candidate")
        self.assertEqual(candidate.input_hash, "")

    def test_legacy_and_final_alignment_both_reject_fabricated_quotes_for_unknown_status(self):
        row = self.response["rows"][0] | {"status": "unknown", "quote": "fabricated"}
        self.client.complete.return_value = json.dumps({"reviews": [{"document_id": self.doc.id, "rows": [row]}]})
        with self.assertRaises(ValueError):
            model_suggest_rows(self.features, [self.doc], self.client, True)
        self.response["rows"] = [row]
        self.client.complete.return_value = json.dumps(self.response)
        with self.assertRaises(ValueError):
            self.run_review()

    def test_report_retains_local_provenance_and_model_mapping_separately(self):
        state = NoveltyHarness().run(features=self.features, documents=[self.doc], cutoff="2024-01-01")
        state["reviews"] = [self.run_review(candidates=state["suggestions"], evidence_hits=state["hits"])]
        payload = report_payload(state, "模拟方案", "2024-01-01", [])
        self.assertEqual(payload["evidence_alignment"]["primary"], "model_semantics")
        self.assertEqual(payload["retrieval_hints"], state["hits"])
        self.assertEqual(payload["candidate_reviews"][0]["rows"][0]["status"], "unknown")
        self.assertEqual(payload["reviews"][0]["rows"][0]["section_id"], "manual-2")
        self.assertIn("证据对照以模型", to_markdown(payload))


if __name__ == "__main__":
    unittest.main()
