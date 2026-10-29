"""排序、候选增量与模型判断层级的离线回归，无真实专利或付费API。"""
import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import Mock

from modules.candidate_ranking import rank_candidates, rule_ranking_hint
from modules.candidate_relevance import (annotate_origins, candidate_origins, fetch_additional_documents,
    incremental_summary, metadata_input_hash, model_judge_candidates, valid_metadata_review)
from modules.google_patent_documents import manual_google_document
from modules.llm_client import LLMClient
from modules.novelty_matching import SynonymLexicon
from modules.novelty_models import Feature
from modules.novelty_report import to_markdown
from skills.report_delivery_skill import build_report as report_payload
from modules.novelty_search import lexical_hint
from modules.novelty_review import model_review_documents
from modules.synonym_expansion import build_expansion_plan
from skills.precheck_skill import NoveltyHarness
from tests.test_model_review import review_response


def candidate(features, lexicon, key, title, content=""):
    return {"publication_id": key, "url": f"https://patents.google.com/patent/{key}/en", "title": title, "content": content,
        "query_ids": ["Q1"], "fetch_status": "not_selected", "fetch_error": "",
        "lexical_hint": lexical_hint(features, title + " " + content, lexicon),
        "observations": [{"id": "Q1:1", "query_id": "Q1", "title": title, "content": content}]}


def metadata_response(key="US123", quote="支架固定导轨", relevance="related", match="partial"):
    return {"publication_id": key, "relevance": relevance, "basis": "模拟：标题包含相关结构关系，正文仍待复核",
        "feature_checks": [{"feature_id": "F1", "match": match, "context_id": "Q1:1", "field": "title",
                            "quote": quote, "basis": "模拟：仅元数据线索"}]}


class CandidateRankingTests(unittest.TestCase):
    def setUp(self):
        self.features = [Feature("F1", "支架固定导轨")]
        self.lexicon = SynonymLexicon([["支架", "托架", "bracket"], ["导轨", "导向轨", "guide rail"]])

    def test_real_relation_and_component_translation_outrank_word_only_index(self):
        noise = candidate(self.features, self.lexicon, "N1", "支架固定导轨名词出现在引用文献目录")
        target = candidate(self.features, self.lexicon, "R1", "bracket guide rail fixture")
        self.assertGreater(noise["lexical_hint"]["score"], target["lexical_hint"]["score"])
        ranked = rank_candidates(self.features, [noise, target], self.lexicon)
        self.assertEqual([c["publication_id"] for c in ranked], ["R1", "N1"])
        self.assertEqual(len(ranked), 2)  # 噪声只降序，不被静默删除。
        self.assertIsNone(ranked[0]["metadata_relevance"])

    def test_directory_technology_and_actual_background_technical_description_are_not_blanket_excluded(self):
        features = [Feature("F1", "目录索引缓存控制装置")]
        actual = candidate(features, self.lexicon, "R1", "目录索引缓存控制装置", "背景描述缓存控制结构")
        self.assertFalse(rule_ranking_hint(features, actual, self.lexicon)["mention_only_hint"])
        target = candidate(self.features, self.lexicon, "R2", "背景技术：支架固定导轨")
        self.assertFalse(rule_ranking_hint(self.features, target, self.lexicon)["mention_only_hint"])

    def test_reversed_control_relation_and_negation_are_ranking_conflicts_not_automatic_verdicts(self):
        features = [Feature("F1", "控制器控制电动机")]
        lexicon = SynonymLexicon()
        right = candidate(features, lexicon, "R", "控制器控制电动机")
        reverse = candidate(features, lexicon, "N", "电动机控制控制器")
        negative = candidate(features, lexicon, "X", "控制器不控制电动机")
        self.assertEqual(rule_ranking_hint(features, reverse, lexicon)["feature_checks"][0]["relation_hint"], "reversed_relation_hint")
        ranked = rank_candidates(features, [negative, reverse, right], lexicon)
        self.assertEqual(ranked[0]["publication_id"], "R")
        self.assertTrue(all(c["metadata_relevance"] is None for c in ranked))
        self.assertEqual(negative["ranking_hint"]["feature_checks"][0]["relation_hint"], "negation_conflict_hint")

    def test_passive_control_statement_does_not_reverse_the_actual_relation(self):
        features = [Feature("F1", "controller controls electric motor")]
        lexicon = SynonymLexicon()
        value = candidate(features, lexicon, "R", "electric motor controlled by controller")
        check = rule_ranking_hint(features, value, lexicon)["feature_checks"][0]
        self.assertEqual(check["relation_hint"], "ordered_relation_hint")
        self.assertFalse(check["contradiction_hint"])

    def test_numeric_disagreement_and_units_keep_case(self):
        features = [Feature("F1", "压力传感器阈值10 kPa")]
        lexicon = SynonymLexicon()
        right = candidate(features, lexicon, "R", "压力传感器阈值10 kPa")
        other = candidate(features, lexicon, "N", "压力传感器阈值100 kPa")
        self.assertTrue(rule_ranking_hint(features, other, lexicon)["feature_checks"][0]["numeric_conflict_hint"])
        self.assertEqual(rank_candidates(features, [other, right], lexicon)[0]["publication_id"], "R")

    def test_known_unit_conversion_and_milli_mega_case_are_distinct(self):
        lexicon = SynonymLexicon()
        features = [Feature("F1", "压力传感器阈值10 kPa")]
        equivalent = candidate(features, lexicon, "R", "压力传感器阈值10000 Pa")
        self.assertFalse(rule_ranking_hint(features, equivalent, lexicon)["feature_checks"][0]["numeric_conflict_hint"])
        features = [Feature("F1", "功率1 mW")]
        different = candidate(features, lexicon, "N", "功率1 MW")
        self.assertTrue(rule_ranking_hint(features, different, lexicon)["feature_checks"][0]["numeric_conflict_hint"])

    def test_ranges_are_not_treated_as_conflicting_single_values(self):
        features = [Feature("F1", "压力传感器阈值10–20 kPa")]
        lexicon = SynonymLexicon()
        value = candidate(features, lexicon, "R", "压力传感器阈值15 kPa")
        self.assertFalse(rule_ranking_hint(features, value, lexicon)["feature_checks"][0]["numeric_conflict_hint"])

    def test_different_query_snippets_are_preserved_and_not_only_first_seen_text(self):
        value = candidate(self.features, self.lexicon, "R", "支架固定导轨名词出现在引用文献目录")
        value["observations"].append({"id": "Q2:1", "query_id": "Q2", "title": "支架固定导轨的装置", "content": "技术结构"})
        hint = rule_ranking_hint(self.features, value, self.lexicon)
        self.assertEqual(hint["context_id"], "Q2:1")
        self.assertFalse(hint["mention_only_hint"])

    def test_gold_style_words_in_metadata_do_not_determine_the_rank(self):
        a = candidate(self.features, self.lexicon, "A", "支架固定导轨", "不相关")
        b = candidate(self.features, self.lexicon, "B", "支架固定导轨", "相关")
        self.assertEqual(rule_ranking_hint(self.features, a, self.lexicon)["score"], rule_ranking_hint(self.features, b, self.lexicon)["score"])


class MetadataModelTests(unittest.TestCase):
    def setUp(self):
        self.features = [Feature("F1", "支架固定导轨")]
        self.lexicon = SynonymLexicon()
        self.candidate = candidate(self.features, self.lexicon, "US123", "支架固定导轨")
        self.client = LLMClient("test-key", "https://example.test", "test-model")
        self.client.complete = Mock(return_value=json.dumps({"candidates": [metadata_response()]}))

    def run_model(self, values=None, consent=True):
        if values is not None:
            self.client.complete.return_value = json.dumps({"candidates": values})
        return model_judge_candidates(self.features, [self.candidate], self.client, consent)

    def test_consent_bound_payload_and_metadata_provenance_never_create_body_review(self):
        with self.assertRaises(PermissionError):
            self.run_model(consent=False)
        self.client.complete.assert_not_called()
        result = self.run_model()
        review = result["reviews"]["US123"]
        self.assertTrue(valid_metadata_review(review, self.features, self.candidate))
        self.assertEqual(review["source"], "model_metadata")
        self.assertNotIn("reviewed", review)
        self.assertNotIn("test-key", json.dumps(result))

    def test_missing_duplicate_or_unverifiable_output_rejects_entire_batch(self):
        for values in ([], [metadata_response(), metadata_response()], [metadata_response(quote="fabricated")],
                       [metadata_response(key="OTHER")]):
            with self.subTest(values=values):
                result = self.run_model(values)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["reviews"], {})

    def test_changed_features_context_or_output_invalidates_cached_model_judgment(self):
        review = self.run_model()["reviews"]["US123"]
        self.assertFalse(valid_metadata_review(review, [Feature("F1", "其他结构")], self.candidate))
        changed = copy.deepcopy(self.candidate)
        changed["observations"][0]["title"] += " changed"
        self.assertFalse(valid_metadata_review(review, self.features, changed))
        self.assertFalse(valid_metadata_review(review | {"relevance": "unrelated"}, self.features, self.candidate))

    def test_unknown_stays_available_and_model_related_is_only_a_ranking_tier(self):
        unknown = metadata_response(relevance="unknown", match="unknown", quote="")
        result = self.run_model([unknown])
        self.assertEqual(result["status"], "ok")
        ranked = rank_candidates(self.features, [self.candidate], self.lexicon, metadata_reviews=result["reviews"])
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["metadata_relevance"]["relevance"], "unknown")

    def test_pair_and_payload_limits_stop_before_api(self):
        with self.assertRaises(ValueError):
            model_judge_candidates(self.features * 81, [self.candidate], self.client, True)
        large = copy.deepcopy(self.candidate)
        large["observations"][0]["content"] = "x" * 40001
        with self.assertRaises(ValueError):
            model_judge_candidates(self.features, [large], self.client, True)
        self.client.complete.assert_not_called()

    def test_service_failure_has_cost_and_no_partial_success(self):
        self.client.complete.side_effect = RuntimeError("模拟服务失败")
        result = self.run_model()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["cost"]["llm_calls"], 1)
        self.assertEqual(result["reviews"], {})


class IncrementalRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.features = [Feature("F1", "支架固定导轨")]
        self.lexicon = SynonymLexicon([["支架", "托架", "bracket"], ["导轨", "导向轨", "guide rail"]])
        self.docs = {key: replace(manual_google_document(f"https://patents.google.com/patent/{key}/en", "模拟来源", "2023-01-01",
            "支架固定导轨。", complete=True), source_kind="synthetic_eval") for key in ("US1001", "US1002", "US1003", "US1004")}
        def search(query, limit):
            if query == "支架固定导轨":
                return [{"url": self.docs["US1001"].url, "title": "支架固定导轨"},
                        {"url": self.docs["US1002"].url, "title": "bracket guide rail fixture"}]
            return [{"url": self.docs["US1003"].url, "title": "托架固定导向轨的装置"},
                    {"url": self.docs["US1004"].url, "title": "支架固定导轨名词出现在引用文献目录"},
                    {"url": self.docs["US1001"].url, "title": "支架固定导轨"}]
        self.searcher = Mock(side_effect=search)
        self.fetcher = Mock(side_effect=lambda url: self.docs[url.split("/patent/")[1].split("/")[0]])
        self.harness = NoveltyHarness(searcher=self.searcher, fetcher=self.fetcher, lexicon=self.lexicon,
                                     max_documents=2, allow_synthetic=True)

    def state(self):
        return self.harness.run(features=self.features, cutoff="2024-01-01",
            expansion=build_expansion_plan(self.features, self.lexicon, "engineering"))

    def test_new_candidate_gets_fetched_but_is_not_related_until_body_review(self):
        state = self.state()
        self.assertIn("US1003", [d.id for d in state["documents"]])
        origins = {c["publication_id"]: c["retrieval_origin"] for c in state["search_audit"]["candidates"]}
        self.assertEqual(origins["US1001"], "both")
        self.assertEqual(origins["US1003"], "expansion_only")
        self.assertEqual(state["retrieval_increment"]["observed_new_body_related_ids"], [])
        self.assertEqual(state["decision"].label, "insufficient_evidence")

    def test_body_related_new_document_is_counted_without_claiming_complete_disclosure(self):
        state = self.state()
        doc = self.docs["US1003"]
        client = LLMClient("test-key", "https://example.test", "test-model")
        client.complete = Mock(return_value=json.dumps(review_response(doc.id, quote="支架固定导轨", status="unknown")))
        review = model_review_documents(self.features, [doc], "2024-01-01", client, True)[0]
        checked = self.harness.run(features=self.features, cutoff="2024-01-01", documents=state["documents"], reviews=[review], search_audit=state["search_audit"])
        gain = checked["retrieval_increment"]
        self.assertEqual(gain["attributable_new_body_related_ids"], ["US1003"])
        self.assertEqual(checked["decision"].label, "insufficient_evidence")
        report = report_payload(checked, "模拟输入", "2024-01-01", self.lexicon.groups)
        self.assertEqual(report["retrieval_increment"], gain)
        self.assertIn("扩展新增文献及正文相关性", to_markdown(report))

    def test_metadata_related_does_not_count_as_body_related(self):
        state = self.state()
        candidate_value = next(c for c in state["search_audit"]["candidates"] if c["publication_id"] == "US1003")
        client = LLMClient("test-key", "https://example.test", "test-model")
        response = metadata_response("US1003", quote="托架固定导向轨")
        response["feature_checks"][0]["context_id"] = candidate_value["observations"][0]["id"]
        client.complete = Mock(return_value=json.dumps({"candidates": [response]}))
        result = model_judge_candidates(self.features, [candidate_value], client, True)
        self.assertEqual(result["status"], "ok")
        audit = copy.deepcopy(state["search_audit"])
        audit["candidates"] = rank_candidates(self.features, audit["candidates"], self.lexicon, metadata_reviews=result["reviews"])
        gain = incremental_summary(self.features, "2024-01-01", audit, state["documents"], [])
        self.assertEqual(gain["new_metadata_related_ids"], ["US1003"])
        self.assertEqual(gain["observed_new_body_related_ids"], [])

    def test_incomplete_original_baseline_does_not_claim_attributable_gain(self):
        state = self.state()
        audit = copy.deepcopy(state["search_audit"])
        audit["queries"][0]["status"] = "error"
        self.assertFalse(candidate_origins(audit, self.features)[2])
        gain = incremental_summary(self.features, "2024-01-01", audit, state["documents"], [])
        self.assertFalse(gain["baseline_complete"])
        self.assertTrue(all(not row["attributable_increment"] for row in gain["rows"]))

    def test_append_fetch_preserves_sources_and_records_partial_failure(self):
        state = self.state()
        selected = [c["publication_id"] for c in state["search_audit"]["candidates"] if c["publication_id"] not in {d.id for d in state["documents"]}]
        fetch = Mock(side_effect=[self.docs[selected[0]], RuntimeError("模拟失败")])
        docs, audit = fetch_additional_documents(self.features, state["documents"], state["search_audit"], selected, fetch)
        self.assertTrue({d.id for d in state["documents"]} <= {d.id for d in docs})
        self.assertEqual(len(docs), 3)
        self.assertEqual(audit["retrieval_cost"]["additional_page_attempts"], 2)
        self.assertEqual(audit["additional_fetches"][1]["status"], "error")
        self.assertNotIn("additional_fetches", state["search_audit"])

    def test_bad_append_selection_never_fetches(self):
        state = self.state()
        fetch = Mock()
        for selected in (["missing"], [state["documents"][0].id], ["US1004"] * 2):
            with self.assertRaises(ValueError):
                fetch_additional_documents(self.features, state["documents"], state["search_audit"], selected, fetch)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
