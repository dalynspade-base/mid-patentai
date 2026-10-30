"""预筛业务与取证的工程边界；所有来源均为明确标记的教学快照。"""

from __future__ import annotations

import json
import math
import unittest
from dataclasses import replace
from unittest.mock import Mock

from evals.legacy.fixtures import case_inputs, dataset
from modules.google_patent_documents import fetch_google_document, manual_google_document, parse_google_page
from modules.novelty_matching import SynonymLexicon
from modules.novelty_models import EvidenceRow, Feature, PatentDocument, SourceSection, google_patent_url, parse_features
from modules.novelty_rules import assess_novelty
from modules.harness import NoveltyHarness
from modules.harness import model_suggest_rows, suggest_rows
from modules.evidence_retriever import PatentRetriever


class PrecheckFlowTests(unittest.TestCase):
    def test_all_labeled_boundary_cases(self):
        harness = NoveltyHarness(allow_synthetic=True)
        for case in dataset()["cases"]:
            with self.subTest(case=case["id"]):
                state = harness.run(**case_inputs(case))
                self.assertEqual(state["decision"].label, case["expected"])
                self.assertEqual(state["trace"][-1], "decide")

    def test_failed_search_stops_without_model_or_fake_evidence(self):
        searcher = Mock(side_effect=RuntimeError("Google Patents timeout"))
        fetcher = Mock()
        state = NoveltyHarness(searcher=searcher, fetcher=fetcher).run(feature_text="压力传感器", cutoff="2024-01-01")
        self.assertEqual(state["decision"].label, "insufficient_evidence")
        self.assertLessEqual(searcher.call_count, 4)
        fetcher.assert_not_called()

    def test_expansion_search_deduplicates_publication_languages(self):
        doc = manual_google_document("https://patents.google.com/patent/US123/en", "测试", "2023-01-01", "压力传感器")
        searcher = Mock(return_value=[{"url": doc.url}, {"url": doc.url.replace("/en", "/zh")}])
        fetcher = Mock(return_value=doc)
        state = NoveltyHarness(searcher=searcher, fetcher=fetcher).run(feature_text="pressure transducer", cutoff="2024-01-01")
        self.assertEqual(len(state["documents"]), 1)
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(state["decision"].label, "insufficient_evidence")

    def test_empty_snapshots_do_not_use_network(self):
        searcher = Mock()
        NoveltyHarness(searcher=searcher).run(feature_text="A", cutoff="2024-01-01", documents=[])
        searcher.assert_not_called()

    def test_fto_requests_do_not_use_network(self):
        searcher = Mock()
        state = NoveltyHarness(searcher=searcher).run(task="fto", feature_text="A")
        self.assertEqual(state["decision"].label, "out_of_scope")
        searcher.assert_not_called()

    def test_features_preserve_relations_numbers_and_negation(self):
        features = parse_features("1. 控制器不启动电动机\n2. 压力传感器串联控制器，阈值为10 kPa")
        self.assertEqual(len(features), 2)
        self.assertIn("不启动", features[0].text)
        self.assertIn("串联", features[1].text)
        self.assertIn("10 kPa", features[1].text)

    def test_material_gaps_block_absence_without_hiding_existing_risk(self):
        for label in ("no_complete_disclosure_found", "potential_novelty_risk"):
            case = next(c for c in dataset()["cases"] if c["expected"] == label)
            inputs = case_inputs(case)
            self.assertEqual(assess_novelty(**inputs, allow_synthetic=True).label, label)
            for context in ({"material_unknowns": ["关键连接方式未定"]},
                            {"material_warnings": ["附件引文未机器校验"]}):
                with self.subTest(label=label, context=context):
                    state = NoveltyHarness(allow_synthetic=True).run(**inputs, **context)
                    expected = "potential_novelty_risk" if label == "potential_novelty_risk" else "insufficient_evidence"
                    self.assertEqual(state["decision"].label, expected)
                    self.assertTrue(state["decision"].warnings)


class EvidenceReadingTests(unittest.TestCase):

    def test_unknown_terms_have_finite_scores_and_do_not_match(self):
        doc = PatentDocument("D1", "测试", "https://patents.google.com/patent/D1", "2023-01-01", (SourceSection("s1", "ABC"),))
        hits = PatentRetriever([doc]).retrieve("完全未见过的词", 1)
        self.assertTrue(math.isfinite(hits[0].score))
        self.assertEqual(hits[0].match_basis, "unresolved")

    def test_differences_are_not_normalized_into_synonyms(self):
        lexicon = SynonymLexicon()
        for left, right in [("温度传感器", "压力传感器"), ("串联", "并联"),
                            ("10 kPa", "100 kPa"), ("包括控制器", "不包括控制器")]:
            with self.subTest(left=left, right=right):
                self.assertNotEqual(lexicon.canonicalize(left), lexicon.canonicalize(right))
        self.assertEqual(lexicon.canonicalize("Pressure Sensor"), lexicon.canonicalize("压力感测器"))

    def test_negated_synonym_candidate_never_auto_disclosed(self):
        doc = PatentDocument("D1", "测试", "https://patents.google.com/patent/D1", "2023-01-01",
                             (SourceSection("s1", "该装置不包括 pressure transducer。"),))
        suggestion = suggest_rows([Feature("F1", "压力传感器")], [doc], SynonymLexicon())[0]
        self.assertEqual(suggestion.rows[0].status, "unknown")
        self.assertFalse(suggestion.reviewed)

    def test_conflicting_custom_synonyms_are_rejected(self):
        with self.assertRaises(ValueError):
            SynonymLexicon([["压力传感器", "sensor"], ["温度传感器", "sensor"]])

    def test_custom_group_can_extend_an_existing_alias(self):
        base = SynonymLexicon()
        lexicon = SynonymLexicon(base.groups + [["压力感测器", "pressure sensing transducer"]])
        self.assertEqual(lexicon.canonicalize("pressure sensing transducer"), lexicon.canonicalize("压力传感器"))


class SyntheticSourceBoundaryTests(unittest.TestCase):

    def test_synthetic_cases_cannot_enter_production(self):
        inputs = case_inputs(dataset()["cases"][0])
        self.assertEqual(assess_novelty(**inputs).label, "insufficient_evidence")


class SourceAndModelBoundaryTests(unittest.TestCase):
    def test_exported_snapshot_roundtrip_and_tampering(self):
        doc = manual_google_document("https://patents.google.com/patent/US123", "标题", "2023-01-01", "原文段落")
        exported = doc.to_dict() | {"snapshot_hash": doc.snapshot_hash}
        self.assertEqual(PatentDocument.from_dict(exported), doc)
        exported["publication_date"] = "2025-01-01"
        with self.assertRaises(ValueError):
            PatentDocument.from_dict(exported)

    def test_google_page_extracts_dates_and_flat_claims_without_combining(self):
        html = '''<meta name="DC.title" content="Pressure system">
        <meta itemprop="publicationDate" content="2023-06-01">
        <meta itemprop="filingDate" content="2022-06-01">
        <div itemprop="abstract">A pressure device.</div>
        <div class="claim" num="1">Claim 1 pressure sensor.</div>
        <div class="claim" num="2">Claim 2 control unit.</div>
        <div itemprop="description"><div class="description-paragraph">A pressure sensor is connected to a controller.</div></div>'''
        doc = parse_google_page("https://patents.google.com/patent/US123/en", html)
        self.assertEqual(doc.publication_date, "2023-06-01")
        self.assertEqual(sum(s.id.startswith("claim-") for s in doc.sections), 2)
        self.assertFalse(doc.complete)
        self.assertTrue(doc.parsed_complete)

    def test_untrusted_host_and_redirect_are_rejected(self):
        for url in ["https://patents.google.com.evil.test/patent/US1", "https://example.com/patent/US1",
                    "https://patents.google.com/search?q=x"]:
            with self.assertRaises(ValueError):
                google_patent_url(url)
        response = Mock(status_code=302)
        with self.assertRaises(RuntimeError):
            fetch_google_document("https://patents.google.com/patent/US1", get=Mock(return_value=response))

    def test_model_needs_consent_and_unverifiable_quote_is_rejected(self):
        inputs = case_inputs(dataset()["cases"][0])
        client = Mock()
        with self.assertRaises(PermissionError):
            model_suggest_rows(inputs["features"], inputs["documents"], client, False)
        client.complete.assert_not_called()
        rows = [{"feature_id": f"F{i}", "status": "disclosed", "section_id": "s1",
                 "quote": "fabricated evidence", "scheme_id": "g1", "basis": "semantic"} for i in range(1, 4)]
        client.complete.return_value = json.dumps({"reviews": [{"document_id": "D1", "rows": rows}]})
        with self.assertRaises(ValueError):
            model_suggest_rows(inputs["features"], inputs["documents"], client, True)

    def test_model_suggestions_never_count_as_completed_review(self):
        inputs = case_inputs(dataset()["cases"][0])
        client = Mock()
        rows = [as_row(feature) for feature in inputs["features"]]
        client.complete.return_value = json.dumps({"reviews": [{"document_id": "D1", "rows": rows}]})
        result = model_suggest_rows(inputs["features"], inputs["documents"], client, True)
        self.assertFalse(result[0].reviewed)
        self.assertFalse(result[0].same_scheme_confirmed)
        self.assertEqual(result[0].reviewer, "")
        self.assertEqual(result[0].reviewed_at, "")
        self.assertEqual(result[0].input_hash, "")
        self.assertTrue(all(row.origin == "model" for row in result[0].rows))


def as_row(feature):
    return {"feature_id": feature.id, "status": "disclosed", "section_id": "s1", "quote": feature.text,
            "scheme_id": "g1", "basis": "exact"}


if __name__ == "__main__":
    unittest.main()
