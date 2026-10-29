"""固定原案例验证排序修复，新增相关收益与名次变化分别计算。"""
import json
import unittest
from pathlib import Path

from engineering.legacy.synonym_eval import ranking_comparison, ranking_comparison_markdown, run_synonym_evaluation


class RankingEvaluationTests(unittest.TestCase):
    def data(self):
        return json.loads((Path(__file__).parents[2] / "evals/legacy/synonym_retrieval_cases.json").read_text(encoding="utf-8"))

    def test_previous_pollution_case_recovers_without_inventing_new_related_documents(self):
        report = ranking_comparison(self.data(), 3)
        row = next(r for r in report["rows"] if r["case_id"] == "engineering_expansion_can_hurt" and r["arm"] == "llm_filtered")
        self.assertEqual(row["before"]["recall_at_k"], .5)
        self.assertEqual(row["after"]["recall_at_k"], 1)
        self.assertEqual(row["before"]["precision_at_k"], 1/3)
        self.assertEqual(row["after"]["precision_at_k"], 2/3)
        self.assertEqual(row["increment"]["new_relevant_ids"], [])
        self.assertTrue(all(r["same_queries_and_cost"] for r in report["rows"]))
        self.assertIn("新增相关文献", ranking_comparison_markdown(report))

    def test_actual_fixture_increment_is_only_the_previously_unretrieved_relevant_id(self):
        report = run_synonym_evaluation(self.data(), 3)
        row = next(r for r in report["cases"] if r["id"] == "engineering_new_component_name")
        self.assertEqual(row["arms"]["original"]["increment"]["new_relevant_ids"], [])
        self.assertEqual(set(row["arms"]["dictionary"]["increment"]["new_relevant_ids"]), {"R2"})
        self.assertEqual(set(row["arms"]["llm_filtered"]["increment"]["new_relevant_ids"]), {"R2", "R3"})

    def test_missing_original_query_makes_increment_attribution_unknown(self):
        data = self.data()
        case = data["cases"][0]
        case["results_by_query"].pop(case["features"][0]["text"])
        report = run_synonym_evaluation(data, 3)
        for arm in report["cases"][0]["arms"].values():
            self.assertFalse(arm["increment"]["baseline_complete"])
            self.assertIsNone(arm["increment"]["new_relevant_ids"])


if __name__ == "__main__":
    unittest.main()
