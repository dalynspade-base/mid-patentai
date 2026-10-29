"""Source location and review provenance regressions; no real legal conclusions."""
import json
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from evals.legacy.fixtures import case_inputs, dataset
from modules.google_patent_documents import manual_google_document, parse_google_page
from modules.novelty_models import (DocumentReview, EvidenceRow, Feature, PatentDocument,
                                    review_input_hash, stamp_review)
from modules.novelty_report import to_markdown
from skills.report_delivery_skill import build_report as report_payload
from modules.novelty_review import adopt_candidate_quotes, edited_review_rows, empty_review_rows
from modules.novelty_rules import assess_novelty


class EvidenceReviewTests(unittest.TestCase):
    def valid_inputs(self):
        features = [Feature("F1", "压力传感器")]
        document = manual_google_document("https://patents.google.com/patent/US123/en", "构造快照",
                                          "2023-01-01", "装置包括压力传感器。", complete=True)
        document = replace(document, source_kind="synthetic_eval")
        row = EvidenceRow("F1", "disclosed", "manual-1", "压力传感器", "S1",
                          "教学夹具：本句明确包含该部件", origin="human")
        review = DocumentReview(document.id, (row,), True, True, True,
                                reviewer="test-fixture-not-a-real-reviewer",
                                same_scheme_basis="教学夹具：该部件属于同一段描述的装置",
                                scheme_section_ids=("manual-1",), relevance="related",
                                relevance_basis="教学夹具：同一压力传感装置", relevance_section_ids=("manual-1",))
        return features, document, stamp_review(review, features, document, "2024-01-01")

    def decide(self, features, document, review, cutoff="2024-01-01"):
        return assess_novelty(features, [document], [review], cutoff, allow_synthetic=True)

    def test_claim_number_does_not_count_abstract(self):
        html = '''<div itemprop="abstract">An example abstract.</div>
        <div class="claim" num="00001" id="CLM-00001">1. A pressure sensor.</div>
        <div class="claim" num="00005" id="CLM-00005">5. A spring.</div>
        <div itemprop="description"><div class="description-paragraph" num="0001" id="p-0001">Description text.</div></div>'''
        document = parse_google_page("https://patents.google.com/patent/US123/en", html)
        sections = {s.id: s for s in document.sections}
        self.assertIn("claim-1", sections)
        self.assertIn("claim-5", sections)
        self.assertEqual(sections["claim-5"].source_location, "权利要求5")
        self.assertEqual(sections["claim-5"].source_anchor, "CLM-00005")

    def test_checked_booleans_without_review_identity_are_insufficient(self):
        case = next(c for c in dataset()["cases"] if c["expected"] == "potential_novelty_risk")
        inputs = case_inputs(case)
        inputs["reviews"] = [replace(r, reviewed=True, reviewer="", reviewed_at="", input_hash="")
                             for r in inputs["reviews"]]
        self.assertEqual(assess_novelty(**inputs, allow_synthetic=True).label, "insufficient_evidence")

    def test_parser_hint_never_confirms_complete_context(self):
        document = parse_google_page("https://patents.google.com/patent/US123/en", '''
        <div class="claim" num="1">1. A sensor.</div>
        <div itemprop="description"><div class="description-paragraph" num="0001">A sensor description.</div></div>''')
        self.assertTrue(document.parsed_complete)
        self.assertFalse(document.complete)
        self.assertEqual(document.sections[-1].source_location, "说明书[0001]")

    def test_missing_source_number_is_labelled_snapshot_local(self):
        document = parse_google_page("https://patents.google.com/patent/US123/en",
                                     '<div class="claim">An unnumbered claim.</div>')
        self.assertEqual(document.sections[0].id, "claim-snapshot-1")
        self.assertIn("原文未编号", document.sections[0].source_location)

    def test_nested_claim_wrapper_is_not_combined_evidence(self):
        document = parse_google_page("https://patents.google.com/patent/US123/en", '''
        <div itemprop="claim"><div class="claim" num="1">1. A sensor.</div>
        <div class="claim" num="2">2. A controller.</div></div>''')
        self.assertEqual([s.id for s in document.sections], ["claim-1", "claim-2"])

    def test_numbered_claim_without_anchor_does_not_invent_one(self):
        document = parse_google_page("https://patents.google.com/patent/US123/en", '''
        <div class="claim"><div class="claim" num="1">1. A sensor.</div>
        <div class="claim" num="5">5. A spring.</div></div>''')
        self.assertEqual([s.id for s in document.sections], ["claim-1", "claim-5"])
        self.assertEqual(document.sections[1].source_location, "权利要求5")
        self.assertEqual(document.sections[1].source_anchor, "")

    def test_legacy_real_snapshots_keep_original_hashes_and_bytes(self):
        root = Path(__file__).resolve().parents[1] / "reports" / "engineering_trial"
        for name in ("US4968168A_source.json", "US5944436A_source.json"):
            path = root / name
            before = path.read_bytes()
            exported = json.loads(before.decode("utf-8"))
            document = PatentDocument.from_dict(exported)
            self.assertEqual(document.snapshot_hash, exported["snapshot_hash"])
            self.assertEqual(document.snapshot_schema_version, 1)
            self.assertNotIn("snapshot_schema_version", document.to_dict())
            self.assertEqual(path.read_bytes(), before)

    def test_new_locator_metadata_is_hashed(self):
        document = parse_google_page("https://patents.google.com/patent/US123/en",
                                     '<div class="claim" num="1" id="c1">1. A sensor.</div>')
        data = document.to_dict() | {"snapshot_hash": document.snapshot_hash}
        self.assertEqual(PatentDocument.from_dict(data), document)
        data["sections"][0]["source_location"] = "权利要求999"
        with self.assertRaises(ValueError):
            PatentDocument.from_dict(data)

    def test_candidate_adoption_copies_quotes_without_confirming_disclosure(self):
        rows = empty_review_rows([Feature("F1", "压力传感器")])
        candidate = EvidenceRow("F1", "disclosed", "c1", "压力传感器", "S1", "explicit", origin="model")
        adopted = adopt_candidate_quotes(rows, (candidate,), ["F1"])[0]
        self.assertEqual(adopted.quote, "压力传感器")
        self.assertEqual(adopted.section_id, "c1")
        self.assertEqual(adopted.origin, "model")
        self.assertEqual(adopted.status, "unknown")
        self.assertEqual(adopted.scheme_id, "")
        self.assertEqual(adopted.basis, "")

    def test_state_change_requires_updated_explanation(self):
        _, _, review = self.valid_inputs()
        value = asdict(review.rows[0]) | {"status": "not_found"}
        rows = edited_review_rows([value], review.rows)
        self.assertEqual(rows[0].basis, "")
        value["basis"] = "教学夹具：重新核对后，本篇没有对应限定"
        self.assertEqual(edited_review_rows([value], review.rows)[0].basis, value["basis"])

    def test_complete_submission_is_bound_and_can_report_risk(self):
        features, document, review = self.valid_inputs()
        self.assertEqual(self.decide(features, document, review).label, "potential_novelty_risk")
        self.assertTrue(review.reviewed_at)
        self.assertEqual(review.input_hash, review_input_hash(features, document, "2024-01-01", review))

    def test_status_and_checkboxes_without_quotes_are_rejected(self):
        features, document, review = self.valid_inputs()
        row = replace(empty_review_rows(features)[0], status="disclosed")
        review = stamp_review(replace(review, rows=(row,)), features, document, "2024-01-01")
        self.assertEqual(self.decide(features, document, review).label, "insufficient_evidence")

    def test_editing_any_bound_input_invalidates_old_review(self):
        features, document, review = self.valid_inputs()
        variants = [([Feature("F1", "温度传感器")], document, review, "2024-01-01"),
                    (features, replace(document, publication_date="2022-01-01"), review, "2024-01-01"),
                    (features, document, review, "2024-01-02"),
                    (features, document, replace(review, reviewer="other"), "2024-01-01"),
                    (features, document, replace(review, rows=(replace(review.rows[0], status="not_found"),)), "2024-01-01")]
        for args in variants:
            with self.subTest(args=args):
                self.assertEqual(self.decide(*args).label, "insufficient_evidence")

    def test_invalid_identity_or_naive_time_is_not_accepted_even_with_new_hash(self):
        features, document, review = self.valid_inputs()
        for changes in ({"reviewer": ""}, {"reviewed_at": ""}, {"reviewed_at": "2024-01-01T00:00:00"}):
            changed = replace(review, **changes)
            changed = replace(changed, input_hash=review_input_hash(features, document, "2024-01-01", changed))
            self.assertEqual(self.decide(features, document, changed).label, "insufficient_evidence")

    def test_same_scheme_string_alone_is_not_enough(self):
        features, document, review = self.valid_inputs()
        for changes in ({"same_scheme_basis": ""}, {"scheme_section_ids": ()},
                        {"scheme_section_ids": ("missing-section",)}):
            changed = stamp_review(replace(review, **changes), features, document, "2024-01-01")
            self.assertEqual(self.decide(features, document, changed).label, "insufficient_evidence")

    def test_report_records_identity_time_provenance_and_original_anchor(self):
        features, _, _ = self.valid_inputs()
        document = parse_google_page("https://patents.google.com/patent/US123/en", '''
        <meta itemprop="publicationDate" content="2023-01-01">
        <div class="claim" num="1" id="CLM-00001">1. 压力传感器。</div>''')
        document = replace(document, complete=True, source_kind="synthetic_eval")
        row = EvidenceRow("F1", "disclosed", "claim-1", "压力传感器", "S1", "教学夹具逐字支持", origin="human")
        review = stamp_review(DocumentReview(document.id, (row,), True, True, True,
            reviewer="test-alias", same_scheme_basis="教学夹具的同一句话", scheme_section_ids=("claim-1",)),
            features, document, "2024-01-01")
        decision = self.decide(features, document, review)
        payload = report_payload({"features": features, "documents": [document], "reviews": [review],
                                  "decision": decision, "trace": []}, "构造输入", "2024-01-01", [])
        markdown = to_markdown(payload)
        self.assertEqual(payload["report_schema_version"], 3)
        self.assertIn(review.reviewed_at, markdown)
        self.assertIn("test-alias", markdown)
        self.assertIn("权利要求1", markdown)
        self.assertIn("#CLM-00001", markdown)
        self.assertIn("人工填写", markdown)

    def test_legacy_report_does_not_relabel_internal_id_as_claim_number(self):
        path = Path(__file__).resolve().parents[1] / "reports" / "engineering_trial" / "original_evidence.json"
        markdown = to_markdown(json.loads(path.read_text(encoding="utf-8")))
        self.assertIn("旧版快照内部编号 claim-2（非原文权利要求号）", markdown)
        self.assertIn("历史记录未绑定", markdown)


if __name__ == "__main__":
    unittest.main()
