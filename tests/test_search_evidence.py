"""Search regressions use injected network functions, never a preset patent verdict."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import parse_qs

from evals.legacy.fixtures import case_inputs, dataset
from modules.google_patent_documents import manual_google_document
from modules.novelty_matching import SynonymLexicon
from modules.novelty_models import DocumentReview, EvidenceRow, Feature, stamp_review
from modules.novelty_report import to_markdown
from skills.report_delivery_skill import build_report as report_payload
from modules.novelty_rules import assess_novelty
from modules.novelty_search import query_plan, search_warnings
from modules.patent_search import search_patents
from skills.precheck_skill import NoveltyHarness


class SearchEvidenceTests(unittest.TestCase):
    def absence_inputs(self):
        case = next(c for c in dataset()["cases"] if c["expected"] == "no_complete_disclosure_found")
        return case_inputs(case)

    def test_overlong_query_is_rejected_before_network(self):
        get = Mock()
        with self.assertRaisesRegex(ValueError, "400"):
            search_patents("x" * 401, get=get)
        get.assert_not_called()

    def test_each_engineering_feature_is_sent_even_after_document_cap(self):
        example = json.loads((Path(__file__).parents[1] / "examples/engineering_trial.json").read_text(encoding="utf-8"))
        features = example["features"]
        doc = manual_google_document("https://patents.google.com/patent/US123/en", "fixture", "2023-01-01", "fixture")
        searcher = Mock(return_value=[{"url": doc.url}])
        state = NoveltyHarness(searcher=searcher, fetcher=Mock(return_value=doc), max_documents=1).run(
            feature_text="\n".join(f["text"] for f in features), cutoff="2024-01-01")
        sent = [call.args[0] for call in searcher.call_args_list]
        self.assertLessEqual(len(sent), 8)
        for feature in features:
            self.assertIn(feature["text"], sent)
        self.assertEqual(len(state["documents"]), 1)

    def test_query_boundary_transmits_every_character(self):
        query = "x" * 399 + "齿"
        response = Mock()
        response.json.return_value = {}
        get = Mock(return_value=response)
        search_patents("  " + query + "  ", get=get)
        self.assertEqual(parse_qs(get.call_args.kwargs["params"]["url"])["q"], [query])

    def test_all_original_features_precede_optional_synonyms(self):
        features = [Feature("F1", "pressure sensor"), Feature("F2", "wireless module")]
        plan = query_plan(features, "", SynonymLexicon())
        self.assertEqual([q["kind"] for q in plan["queries"][:2]], ["original", "original"])
        self.assertEqual([q["feature_ids"] for q in plan["queries"][:2]], [["F1"], ["F2"]])
        self.assertLessEqual(len(plan["queries"]), 8)

    def test_original_query_case_is_preserved_for_units(self):
        features = [Feature("F1", "Power is 1 mW"), Feature("F2", "Power is 1 MW")]
        plan = query_plan(features, "", SynonymLexicon())
        self.assertEqual([q["query"] for q in plan["queries"]], [f.text for f in features])
        self.assertEqual([q["feature_ids"] for q in plan["queries"]], [["F1"], ["F2"]])

    def test_budget_and_overlong_feature_remain_explicitly_unsearched(self):
        features = [Feature("F1", "x" * 401)] + [Feature(f"F{i}", f"part {i}") for i in range(2, 12)]
        searcher = Mock(return_value=[])
        state = NoveltyHarness(searcher=searcher).run(features=features, cutoff="2024-01-01")
        self.assertEqual(searcher.call_count, 8)
        self.assertTrue(all(len(call.args[0]) <= 400 for call in searcher.call_args_list))
        skipped_ids = {f for s in state["search_audit"]["skipped"] for f in s["feature_ids"]}
        self.assertTrue({"F1", "F10", "F11"} <= skipped_ids)
        warnings = " ".join(state["decision"].warnings)
        self.assertIn("F1", warnings)
        self.assertIn("F11", warnings)
        self.assertEqual(state["decision"].label, "insufficient_evidence")

    def test_failed_empty_invalid_and_unfetched_results_are_retained(self):
        searcher = Mock(side_effect=[RuntimeError("fixture timeout"), [], [
            {"url": "https://example.com/patent/NO", "title": "invalid"},
            {"url": "https://patents.google.com/patent/US123/en", "title": "reservoir"},
            {"url": "https://patents.google.com/patent/US124/en", "title": "spring"}]])
        fetcher = Mock(side_effect=RuntimeError("fixture fetch failure"))
        state = NoveltyHarness(searcher=searcher, fetcher=fetcher, max_documents=1,
                              lexicon=SynonymLexicon([])).run(feature_text="first\nsecond\nthird", cutoff="2024-01-01")
        records = state["search_audit"]["queries"]
        self.assertEqual([r["status"] for r in records], ["error", "ok", "ok"])
        self.assertEqual(records[0]["error"], "fixture timeout")
        self.assertEqual(records[1]["returned_count"], 0)
        self.assertTrue(records[2]["results"][0]["validation_error"])
        self.assertTrue(all(r["started_at"] and r["finished_at"] for r in records))
        candidates = state["search_audit"]["candidates"]
        self.assertEqual({c["fetch_status"] for c in candidates}, {"error", "not_selected"})
        self.assertTrue(any("正文获取失败" in w for w in state["decision"].warnings))
        self.assertTrue(any("未读取正文" in w for w in state["decision"].warnings))
        self.assertEqual(state["decision"].label, "insufficient_evidence")

    def test_candidates_deduplicate_by_publication_and_lexical_hints_do_not_confirm_relevance(self):
        doc = manual_google_document("https://patents.google.com/patent/US123/en", "fixture", "2023-01-01", "pressure sensor")
        searcher = Mock(return_value=[{"url": doc.url, "title": "pressure sensor"},
                                     {"url": doc.url.replace("/en", "/zh"), "title": "压力传感器"}])
        fetcher = Mock(return_value=doc)
        state = NoveltyHarness(searcher=searcher, fetcher=fetcher).run(feature_text="pressure sensor", cutoff="2024-01-01")
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(len(state["search_audit"]["candidates"]), 1)
        self.assertGreater(state["search_audit"]["candidates"][0]["lexical_hint"]["score"], 0)
        self.assertEqual(state["suggestions"][0].relevance, "unknown")
        self.assertEqual(state["decision"].label, "insufficient_evidence")

    def test_online_journal_survives_offline_review_and_json_markdown_export(self):
        searcher = Mock(return_value=[])
        harness = NoveltyHarness(searcher=searcher)
        first = harness.run(feature_text="pressure sensor", cutoff="2024-01-01")
        journal = json.loads(json.dumps(first["search_audit"]))
        searcher.reset_mock()
        second = harness.run(features=first["features"], documents=[], cutoff="2024-01-01", search_audit=journal)
        searcher.assert_not_called()
        self.assertEqual(second["search_audit"], first["search_audit"])
        payload = json.loads(json.dumps(report_payload(second, "fixture", "2024-01-01", [])))
        self.assertEqual(payload["search_audit"], journal)
        markdown = to_markdown(payload)
        self.assertIn(journal["queries"][0]["query"], markdown)
        self.assertIn(journal["queries"][0]["started_at"], markdown)
        self.assertIn("空结果", " ".join(payload["decision"]["warnings"]))

    def test_provided_snapshots_are_explicitly_offline(self):
        searcher = Mock()
        state = NoveltyHarness(searcher=searcher).run(feature_text="part", cutoff="2024-01-01", documents=[])
        searcher.assert_not_called()
        self.assertEqual(state["search_audit"]["mode"], "provided_snapshots")
        self.assertEqual(state["search_audit"]["queries"], [])
        self.assertIn("未执行在线检索", to_markdown(report_payload(state, "fixture", "2024-01-01", [])))

    def test_custom_query_and_reused_journal_do_not_claim_feature_coverage(self):
        features = [Feature("F1", "ink reservoir")]
        state = NoveltyHarness(searcher=Mock(return_value=[])).run(features=features, query="ballpoint", cutoff="2024-01-01")
        self.assertTrue(all(not q["feature_ids"] for q in state["search_audit"]["queries"]))
        self.assertTrue(any("自定义" in w for w in state["decision"].warnings))
        changed = [Feature("F1", "plastic teeth")]
        self.assertTrue(any("不一致" in w for w in search_warnings(state["search_audit"], changed, "2024-01-01")))
        searcher = Mock()
        with self.assertRaisesRegex(ValueError, "400"):
            NoveltyHarness(searcher=searcher).run(features=features, query="x" * 401, cutoff="2024-01-01")
        searcher.assert_not_called()

    def test_unknown_or_unrelated_documents_cannot_support_absence(self):
        for relevance in ("unknown", "unrelated"):
            inputs = self.absence_inputs()
            inputs["reviews"] = [stamp_review(replace(r, relevance=relevance), inputs["features"], d, inputs["cutoff"])
                                 for r, d in zip(inputs["reviews"], inputs["documents"])]
            self.assertEqual(assess_novelty(**inputs, allow_synthetic=True).label, "insufficient_evidence")

    def test_relevance_needs_source_explanation_and_is_bound_to_review(self):
        for changes in ({"relevance_basis": ""}, {"relevance_section_ids": ()},
                        {"relevance_section_ids": ("invented",)}):
            inputs = self.absence_inputs()
            inputs["reviews"] = [stamp_review(replace(r, **changes), inputs["features"], d, inputs["cutoff"])
                                 for r, d in zip(inputs["reviews"], inputs["documents"])]
            self.assertEqual(assess_novelty(**inputs, allow_synthetic=True).label, "insufficient_evidence")
        inputs = self.absence_inputs()
        inputs["reviews"] = [replace(r, relevance_basis="changed explanation") for r in inputs["reviews"]]
        self.assertTrue(any("绑定" in w for w in assess_novelty(**inputs, allow_synthetic=True).warnings))

    def test_unrelated_document_cannot_create_risk_even_with_all_disclosed(self):
        case = next(c for c in dataset()["cases"] if c["expected"] == "potential_novelty_risk")
        inputs = case_inputs(case)
        inputs["reviews"] = [stamp_review(replace(r, relevance="unrelated"), inputs["features"], d, inputs["cutoff"])
                             for r, d in zip(inputs["reviews"], inputs["documents"])]
        self.assertEqual(assess_novelty(**inputs, allow_synthetic=True).label, "insufficient_evidence")

    def test_bounded_online_absence_can_pass_after_actual_review_without_search_gaps(self):
        features = [Feature("F1", "pressure sensor")]
        doc = manual_google_document("https://patents.google.com/patent/US123/en", "synthetic fixture",
            "2023-01-01", "The device estimates pressure from electric current.", complete=True)
        doc = replace(doc, source_kind="synthetic_eval")
        searcher = Mock(return_value=[{"url": doc.url, "title": doc.title}])
        harness = NoveltyHarness(searcher=searcher, fetcher=Mock(return_value=doc),
                                lexicon=SynonymLexicon([]), allow_synthetic=True)
        state = harness.run(features=features, cutoff="2024-01-01")
        review = stamp_review(DocumentReview(doc.id,
            (EvidenceRow("F1", "not_found", basis="教学模拟：已检查电流估算方法，未找到独立传感器", origin="human"),),
            reviewed=True, reviewer="test-fixture-not-real-reviewer", relevance="related",
            relevance_basis="教学模拟：同样用于压力测量", relevance_section_ids=("manual-1",)), features, doc, "2024-01-01")
        checked = harness.run(features=features, cutoff="2024-01-01", documents=[doc], reviews=[review], search_audit=state["search_audit"])
        self.assertEqual(checked["decision"].label, "no_complete_disclosure_found")
        self.assertEqual(searcher.call_count, 1)

    def test_search_gaps_block_absence_but_preserve_a_separately_verified_risk(self):
        for label in ("no_complete_disclosure_found", "potential_novelty_risk"):
            inputs = case_inputs(next(c for c in dataset()["cases"] if c["expected"] == label))
            journal = NoveltyHarness(searcher=Mock(side_effect=RuntimeError("fixture search failed"))).run(
                features=inputs["features"], cutoff=inputs["cutoff"])["search_audit"]
            state = NoveltyHarness(allow_synthetic=True).run(**inputs, search_audit=journal)
            self.assertEqual(state["decision"].label, "potential_novelty_risk" if label == "potential_novelty_risk" else "insufficient_evidence")
            self.assertTrue(any("fixture search failed" in w for w in state["decision"].warnings))


if __name__ == "__main__":
    unittest.main()
