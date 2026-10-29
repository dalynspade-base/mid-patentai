"""Evaluation-contract tests are constructed fixtures, never real professional answer keys."""
import json
import importlib.util
import contextlib
import io
import unittest
import uuid
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

from engineering.legacy.real_patent_eval import (fetch_sources, load_sources, real_cases, run_real_evaluation,
                                      summarize_real, write_json)

ROOT = Path(__file__).resolve().parents[2]


class RealEvaluationTests(unittest.TestCase):
    def folder(self):
        path = ROOT / "tests/.artifacts" / uuid.uuid4().hex
        path.mkdir(parents=True)
        return path

    def sources(self, destination):
        html = '<meta name="DC.title" content="constructed fixture"><meta itemprop="publicationDate" content="1990-01-01"><div class="claim" num="1">1. A constructed test mechanism.</div>'
        response = Mock(status_code=200, text=html, content=html.encode())
        fetch_sources(destination, ["US4968168A", "US5944436A"], get=Mock(return_value=response))

    def observations(self):
        return [{"case_id": "r1", "input_hash": "fixture", "label": "insufficient_evidence"}]

    def reference(self, **kwargs):
        return {"case_id": "r1", "input_hash": "fixture", "provenance": "human_reviewed",
                "author_type": "human",
                "reviewer": "synthetic-test-reference-not-a-real-reviewer", "reviewed_at": "2024-01-01T00:00:00+00:00",
                "label": "potential_novelty_risk", "basis": "unit-test-only constructed reference", **kwargs}

    def test_missing_references_are_null_not_zero_accuracy(self):
        result = summarize_real(self.observations(), [])
        self.assertEqual(result["reference_case_count"], 0)
        self.assertEqual(result["abstention_count"], 1)
        self.assertIsNone(result["risk_miss_count"])
        self.assertIsNone(result["false_clearance_count"])
        self.assertNotIn("all_passed", result)

    def test_ai_draft_cannot_be_promoted_to_reference(self):
        for provenance in ("ai_draft", "unreviewed", "model", "synthetic_eval"):
            result = summarize_real(self.observations(), [self.reference(provenance=provenance)])
            self.assertEqual(result["reference_case_count"], 0)
            self.assertTrue(result["rejected_references"])

    def test_stale_missing_identity_or_unzoned_reference_is_not_scored(self):
        for changes in ({"input_hash": "stale"}, {"reviewer": ""}, {"basis": ""},
                        {"author_type": "ai"}, {"reviewed_at": "2024-01-01T00:00:00"}, {"label": "grantable"}):
            self.assertEqual(summarize_real(self.observations(), [self.reference(**changes)])["reference_case_count"], 0)

    def test_null_or_nontext_reference_identity_and_basis_are_not_scored(self):
        for field in ("reviewer", "basis"):
            for value in (None, False, 123, [], {}):
                with self.subTest(field=field, value=value):
                    result = summarize_real(self.observations(), [self.reference(**{field: value})])
                    self.assertEqual(result["reference_case_count"], 0)
                    self.assertIsNone(result["false_clearance_count"])

    def test_count_definitions_distinguish_missed_risk_clearance_and_refusal(self):
        refused = summarize_real(self.observations(), [self.reference()])
        self.assertEqual((refused["risk_miss_count"], refused["false_clearance_count"], refused["abstention_count"]), (1, 0, 1))
        cleared = summarize_real([self.observations()[0] | {"label": "no_complete_disclosure_found"}], [self.reference()])
        self.assertEqual((cleared["risk_miss_count"], cleared["false_clearance_count"], cleared["abstention_count"]), (1, 1, 0))

    def test_false_alarm_is_separate_from_false_clearance(self):
        result = summarize_real([self.observations()[0] | {"label": "potential_novelty_risk"}],
                                [self.reference(label="no_complete_disclosure_found")])
        self.assertEqual(result["false_alarm_count"], 1)
        self.assertEqual(result["false_clearance_count"], 0)
        self.assertIsNone(result["risk_miss_count"])

    def test_duplicate_references_or_observations_are_rejected(self):
        with self.assertRaises(ValueError):
            summarize_real(self.observations(), [self.reference(), self.reference()])
        with self.assertRaises(ValueError):
            summarize_real(self.observations() * 2, [])

    def test_sources_preserve_raw_html_and_reject_tampering(self):
        source = self.folder() / "sources"
        self.sources(source)
        self.assertEqual(len(load_sources(source, ["US4968168A", "US5944436A"])), 2)
        (source / "US4968168A.html").write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_sources(source, ["US4968168A", "US5944436A"])

    def test_failed_source_keeps_failure_without_fake_snapshot(self):
        source = self.folder() / "sources"
        response = Mock(status_code=503, content=b"unavailable")
        import requests
        response.raise_for_status.side_effect = requests.HTTPError("fixture 503")
        get = Mock(return_value=response)
        with self.assertRaises(ValueError):
            fetch_sources(source, ["US4968168A", "US5944436A"], get=get)
        self.assertEqual(get.call_count, 2)
        self.assertFalse(list(source.glob("*_source.json")))
        self.assertEqual(json.loads((source / "source_manifest.json").read_text())["documents"][0]["status"], "error")

    def test_output_and_source_pack_never_overwrite_existing_directory(self):
        source = self.folder() / "sources"
        self.sources(source)
        with self.assertRaises(FileExistsError):
            fetch_sources(source, ["US4968168A", "US5944436A"], get=Mock())
        output = self.folder()
        with self.assertRaises(FileExistsError):
            run_real_evaluation(ROOT, source, output)

    def test_dataset_has_real_provenance_and_no_preset_answer(self):
        definition, cases, _ = real_cases(ROOT)
        self.assertIn("without_independent_human_gold", definition["provenance"])
        self.assertTrue(all("expected" not in c and "label" not in c for c in cases))
        self.assertEqual(sum(a.text != b.text for a, b in zip(cases[0]["features"], cases[1]["features"])), 1)

    def test_reference_labels_do_not_enter_harness_and_predictions_are_saved_first(self):
        source = self.folder() / "sources"
        self.sources(source)
        output = self.folder() / "run"
        references = self.folder() / "references.json"
        write_json(references, [self.reference(case_id="ballpoint_original", label="no_complete_disclosure_found")])
        from skills.precheck_skill import NoveltyHarness
        harness = NoveltyHarness()
        original_run = harness.run
        calls = []
        def observed_run(**kwargs):
            calls.append(kwargs)
            self.assertFalse(references.exists() and (output / "observations.json").exists())
            return original_run(**kwargs)
        harness.run = observed_run
        report = run_real_evaluation(ROOT, source, output, references, harness=harness)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(set(c) == {"features", "cutoff", "documents"} for c in calls))
        self.assertTrue(all(o["label"] == "insufficient_evidence" for o in report["observations"]))
        self.assertEqual(report["summary"]["reference_case_count"], 0)
        for path in output.glob("*_evidence.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["reviews"], [])
            self.assertTrue(all(not r["reviewed"] for r in payload["candidate_reviews"]))

    def test_engineering_trial_refuses_existing_output_before_requesting_key(self):
        from tools.legacy.run_engineering_trial import main
        directory = self.folder()
        with patch("sys.argv", ["trial", "--source-dir", str(directory), "--output-dir", str(directory)]), \
             patch("getpass.getpass") as key, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                main()
            key.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("deepeval"), "optional historical DeepEval dependency")
    def test_engineering_trial_model_output_stays_candidate_only(self):
        from tools.legacy.run_engineering_trial import main
        directory = self.folder()
        source = directory / "sources"
        self.sources(source)
        output = directory / "trial"
        rows = [{"feature_id": f"F{i}", "status": "unknown", "section_id": "", "quote": "", "scheme_id": "", "basis": "constructed model mock"} for i in range(1, 7)]
        response = json.dumps({"reviews": [{"document_id": pub, "rows": rows} for pub in ("US4968168A", "US5944436A")]})
        with patch("sys.argv", ["trial", "--source-dir", str(source), "--output-dir", str(output), "--seed", "3862674471"]), \
             patch("getpass.getpass", return_value="test-key"), \
             patch("modules.llm_client.LLMClient.complete", return_value=response), \
             patch("modules.patent_search.search_patents", return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        for path in output.glob("*_evidence.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["reviews"], [])
            self.assertTrue(all(not r["reviewed"] for r in payload["candidate_reviews"]))
            self.assertTrue(all(row["origin"] == "model" for r in payload["candidate_reviews"] for row in r["rows"]))


if __name__ == "__main__":
    unittest.main()
