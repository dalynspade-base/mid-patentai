"""Historical DeepEval adapter tests; install requirements-engineering.txt to run."""
import importlib.util
import unittest

HAS_DEEPEVAL = importlib.util.find_spec('deepeval') is not None
if HAS_DEEPEVAL:
    from engineering.legacy.basic_contracts import run_basic_evals


@unittest.skipUnless(HAS_DEEPEVAL, 'optional historical DeepEval dependency is not installed')
class LegacyBasicContractTests(unittest.TestCase):
    def test_synonyms_rank_same_original_source_first(self):
        report = run_basic_evals()
        failures = [case for case in report["cases"] if case["skill"] == "llamaindex" and not case["passed"]]
        self.assertEqual(failures, [])

    def test_basic_report_includes_each_skill_and_judge_negative_controls(self):
        report = run_basic_evals()
        self.assertTrue(report["all_passed"])
        self.assertEqual(set(report["summary"]), {"langgraph", "llamaindex", "deepeval"})
        self.assertIn("synthetic", report["provenance"])
        self.assertEqual(report["summary"]["deepeval"]["total"], 4)
