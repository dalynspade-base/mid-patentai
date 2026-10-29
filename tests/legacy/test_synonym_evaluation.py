"""Historical fixed-pool metrics, never current semantic acceptance."""
import copy
import json
import unittest
from pathlib import Path
from engineering.legacy.synonym_eval import relevance_input_hash, retrieval_metrics, run_synonym_evaluation

class SynonymEvaluationTests(unittest.TestCase):
    def data(self):
        return json.loads((Path(__file__).parents[2] / "evals/legacy/synonym_retrieval_cases.json").read_text(encoding="utf-8"))

    def test_metric_denominators_and_zero_relevant_reference(self):
        metrics = retrieval_metrics(["R1", "N1"], {"R1", "R2"}, {"R1", "R2", "N1"}, 3)
        self.assertEqual(metrics["recall_at_k"], 0.5)
        self.assertEqual(metrics["precision_at_k"], 1/3)
        self.assertIsNone(retrieval_metrics([], set(), set(), 3)["recall_at_k"])
        with self.assertRaises(ValueError):
            retrieval_metrics(["R1", "R1"], {"R1"}, {"R1"}, 3)

    def test_unjudged_or_missing_cached_queries_are_not_counted_as_negatives(self):
        self.assertIsNone(retrieval_metrics(["N1"], {"R1"}, {"R1"}, 1)["precision_at_k"])
        data = self.data()
        case = data["cases"][0]
        case["results_by_query"].pop(case["features"][0]["text"])
        report = run_synonym_evaluation(data, 3)
        self.assertIsNone(report["cases"][0]["arms"]["original"]["metrics"]["recall_at_k"])
        self.assertEqual(report["comparisons"]["original_to_filtered"]["paired_cases"], 3)

    def test_paired_fixture_keeps_improvement_and_degradation_instead_of_only_average(self):
        report = run_synonym_evaluation(self.data(), 3)
        rows = {r["id"]: r for r in report["cases"]}
        good = rows["engineering_new_component_name"]["arms"]
        bad = rows["engineering_expansion_can_hurt"]["arms"]
        old = {r["id"]: r for r in run_synonym_evaluation(self.data(), 3, ranker="legacy")["cases"]}["engineering_expansion_can_hurt"]["arms"]
        self.assertGreater(good["llm_filtered"]["metrics"]["recall_at_k"], good["original"]["metrics"]["recall_at_k"])
        self.assertLess(old["dictionary"]["metrics"]["precision_at_k"], old["original"]["metrics"]["precision_at_k"])
        self.assertEqual(bad["dictionary"]["metrics"]["precision_at_k"], bad["original"]["metrics"]["precision_at_k"])
        self.assertEqual(bad["dictionary"]["increment"]["new_relevant_ids"], [])
        for row in rows.values():
            self.assertTrue(all(len(arm["queries"]) <= row["same_query_cap"] for arm in row["arms"].values()))

    def test_model_labels_cannot_be_presented_as_independent_real_reference(self):
        data = self.data()
        data.update(provenance="google_patents_frozen_pool", reference_author_type="model")
        with self.assertRaises(ValueError):
            run_synonym_evaluation(data)

    def test_bad_cache_ids_and_duplicate_cases_are_rejected(self):
        data = self.data()
        data["cases"].append(copy.deepcopy(data["cases"][0]))
        with self.assertRaises(ValueError):
            run_synonym_evaluation(data)
        data = self.data()
        data["cases"][0]["results_by_query"][data["cases"][0]["features"][0]["text"]]["ids"] = ["invented"]
        with self.assertRaises(ValueError):
            run_synonym_evaluation(data)

    def test_real_reference_requires_bound_human_metadata_and_google_sources(self):
        data = self.data()
        data.update(provenance="google_patents_frozen_pool", reference_author_type="human")
        with self.assertRaises(ValueError):
            run_synonym_evaluation(data)
        for case in data["cases"]:
            case["reference"] = {"author_type": "human", "reviewer": "synthetic-test-not-real-person",
                "basis": "synthetic fixture", "reviewed_at": "2026-10-21T00:00:00+00:00", "input_hash": relevance_input_hash(case)}
        with self.assertRaises(ValueError):
            run_synonym_evaluation(data)  # 合成来源没有真实Google链接，不能仅改旗标冒充真实池。

    def test_reference_labels_do_not_change_queries_or_ranking(self):
        data = self.data()
        before = run_synonym_evaluation(data, 3)
        data["cases"][0]["relevant_ids"] = ["N1"]
        after = run_synonym_evaluation(data, 3)
        for arm in before["cases"][0]["arms"]:
            self.assertEqual(before["cases"][0]["arms"][arm]["queries"], after["cases"][0]["arms"][arm]["queries"])
            self.assertEqual(before["cases"][0]["arms"][arm]["ranked_ids"], after["cases"][0]["arms"][arm]["ranked_ids"])

    def test_unjudged_outside_topk_still_makes_fixed_pool_reference_incomplete(self):
        data = self.data()
        data["cases"][0]["judged_ids"].remove("N2")
        report = run_synonym_evaluation(data, 3)
        self.assertIsNone(report["cases"][0]["arms"]["original"]["metrics"]["recall_at_k"])

    def test_zero_relevant_cases_keep_precision_pairs_without_inventing_recall(self):
        data = self.data()
        data["cases"][0]["relevant_ids"] = []
        report = run_synonym_evaluation(data, 3)
        counts = report["comparisons"]["original_to_filtered"]["metric_pair_counts"]
        self.assertEqual(counts["recall_at_k"], 3)
        self.assertEqual(counts["precision_at_k"], 4)
