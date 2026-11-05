# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TESTSETS = ROOT / "evals" / "testsets"
sys.path.insert(0, str(TESTSETS))

import rsi_loop  # noqa: E402
from rsi_scoring import acceptance, score_pool, score_retrieval, validate_case, validate_judgment  # noqa: E402


FINAL_DOC = rsi_loop.SKILL0 + "\n" + "\n".join(rsi_loop.MOCK_LESSONS)


class RsiTestCase(unittest.TestCase):
    def record(self, pid: str, doc: str = FINAL_DOC) -> dict:
        raw = rsi_loop.mock_judgment(doc, pid)
        judgment, errors = validate_judgment(raw, rsi_loop.public_candidate(pid), rsi_loop.TARGET)
        self.assertEqual([], errors, f"{pid}: {errors}")
        return {"document_id": pid, "valid": True, "judgment": judgment, "errors": []}

    def records(self, ids=None) -> list[dict]:
        return [self.record(pid) for pid in (ids or rsi_loop.ALL_IDS)]

    def test_data_contract_and_difficulty(self):
        self.assertEqual([], validate_case(rsi_loop.CASE, rsi_loop.PATENTS))
        self.assertEqual(26, len(rsi_loop.ALL_IDS))
        self.assertEqual(16, len(rsi_loop.TRAIN_IDS))
        self.assertEqual(10, len(rsi_loop.ANCHOR_IDS))
        hard = [c for c in rsi_loop.CASE["candidates"] if c["difficulty"] == "hard_near_neighbor"]
        self.assertEqual(6, len(hard))
        self.assertTrue(all(c["label"]["prior_art_at_cutoff"] for c in hard))
        self.assertEqual(6, len(rsi_loop.PUBLIC_CASE["targets"][0]["features"]))
        for patent in rsi_loop.PATENTS.values():
            for section in patent["sections"].values():
                self.assertTrue(section["text"].strip())

    def test_public_prompt_has_no_answer_leak(self):
        prompt = rsi_loop.judge_user_prompt("CN106757342A")
        for forbidden in ["CN108559503B", "derived_from", "adjustment_note", "answer_key", "\"label\"", "\"usage\"", "全部落在A的权利要求"]:
            self.assertNotIn(forbidden, prompt)
        self.assertIn('"public_case"', prompt)
        self.assertIn('"candidate"', prompt)
        self.assertNotIn("answer_key", json.dumps(rsi_loop.PUBLIC_CASE, ensure_ascii=False))

    def test_evidence_package_is_sufficient_or_explicitly_limited(self):
        original = rsi_loop.public_candidate("CN108559503B")
        self.assertIn("CN108559503A:claim6", original["sections"])
        self.assertIn("50℃-70℃", original["sections"]["CN108559503A:claim6"]["text"])
        self.assertIn("离心", original["sections"]["CN108559503A:desc-method"]["text"])
        for pid in ["CN106757342A", "CN107325812A", "CN107325004A", "CN108034989A", "CN107093671A", "CN108560056A"]:
            candidate = rsi_loop.public_candidate(pid)
            self.assertTrue(candidate["evidence_scope"]["complete_for_target"])
            self.assertIn(f"{pid}:claim1", candidate["sections"])
        self.assertFalse(rsi_loop.public_candidate("CN111233035A")["evidence_scope"]["complete_for_target"])

    def test_final_mock_is_mechanism_check_not_model_score(self):
        records = self.records()
        train = score_pool(records, rsi_loop.TRAIN_IDS, rsi_loop.CASE)
        anchor = score_pool(records, rsi_loop.ANCHOR_IDS, rsi_loop.CASE)
        self.assertEqual(1.0, train["judgment_coverage"])
        self.assertEqual(1.0, train["candidate_relevance"]["f1"])
        self.assertEqual(1.0, train["judgment_accuracy"])
        self.assertEqual(1.0, anchor["f_signal"])
        self.assertEqual(0, train["full_disclosure_false_positives"])
        self.assertEqual(0, train["full_disclosure_false_negatives"])

    def test_tampered_quote_is_unjudged(self):
        raw = rsi_loop.mock_judgment(FINAL_DOC, "CN108559503B")
        raw["rows"][0]["evidence"][0]["quote"] = "伪造且不在原文中的引文"
        _judgment, errors = validate_judgment(raw, rsi_loop.public_candidate("CN108559503B"), rsi_loop.TARGET)
        self.assertIn("evidence_quote", {e["code"] for e in errors})

    def test_scope_overclaim_is_rejected(self):
        raw = rsi_loop.mock_judgment(FINAL_DOC, "CN111233035A")
        raw["evidence_scope_complete"] = True
        _judgment, errors = validate_judgment(raw, rsi_loop.public_candidate("CN111233035A"), rsi_loop.TARGET)
        self.assertIn("scope_overclaim", {e["code"] for e in errors})

    def test_full_disclosure_false_positive(self):
        records = self.records()
        target = next(r for r in records if r["document_id"] == "CN106757342A")
        judgment = target["judgment"]
        for row in judgment["rows"]:
            row["status"] = "disclosed"
            row["evidence"] = [{"section_id": "CN106757342A:claim1", "quote": rsi_loop.PATENTS["CN106757342A"]["sections"]["CN106757342A:claim1"]["text"]}]
        judgment["coverage"] = "full_disclosure"
        judgment["derived_coverage"] = "full_disclosure"
        train = score_pool(records, rsi_loop.TRAIN_IDS, rsi_loop.CASE)
        self.assertGreaterEqual(train["full_disclosure_false_positives"], 1)
        self.assertFalse(train["per_item"]["CN106757342A"])

    def test_full_disclosure_false_negative_and_date_error(self):
        records = self.records()
        target = next(r for r in records if r["document_id"] == "CN108559503B")["judgment"]
        for row in target["rows"]:
            row["status"] = "not_found"
            row["evidence"] = []
        target["coverage"] = target["derived_coverage"] = "local_association"
        target["prior_art"] = False
        train = score_pool(records, rsi_loop.TRAIN_IDS, rsi_loop.CASE)
        self.assertEqual(1, train["full_disclosure_false_negatives"])
        self.assertEqual(1, train["date_errors"])

    def test_partial_local_confusion(self):
        records = self.records()
        target = next(r for r in records if r["document_id"] == "CN106757342A")["judgment"]
        row = next(row for row in target["rows"] if row["feature_id"] == "F5")
        row["status"] = "not_found"
        row["evidence"] = []
        target["coverage"] = target["derived_coverage"] = "local_association"
        train = score_pool(records, rsi_loop.TRAIN_IDS, rsi_loop.CASE)
        self.assertEqual(1, train["coverage_confusion"])

    def test_missing_result_blocks_f_signal_and_acceptance(self):
        records = self.records()[:-1]
        train = score_pool(records, rsi_loop.TRAIN_IDS, rsi_loop.CASE)
        self.assertLess(train["judgment_coverage"], 1.0)
        self.assertIsNone(train["f_signal"])
        adopted, basis = acceptance("ineq", train, None, {}, 0.03)
        self.assertFalse(adopted)
        self.assertIn("覆盖率不足", basis)

    def test_duplicate_result_is_unjudged(self):
        records = self.records()
        records.append(copy.deepcopy(records[0]))
        train = score_pool(records, rsi_loop.TRAIN_IDS, rsi_loop.CASE)
        self.assertLess(train["judgment_coverage"], 1.0)
        self.assertIn("duplicate", train["error_counts"])

    def test_independent_retrieval_scoring(self):
        result = score_retrieval(["CN108559503B", "UNKNOWN123"], rsi_loop.ALL_IDS, rsi_loop.CASE)
        self.assertEqual(1, result["tp"])
        self.assertEqual(1, result["fp"])
        self.assertEqual(12, result["fn"])
        self.assertIn("CN106757342A", result["missed"])

    def test_sign_rule_reports_small_sample(self):
        pool = {"f_signal": 0.9, "judgment_coverage": 1.0, "per_item": {str(i): True for i in range(16)}}
        adopted, basis = acceptance("sign", pool, 0.8, {str(i): False for i in range(16)}, 0.03)
        self.assertTrue(adopted)
        self.assertIn("n=16", basis)
        self.assertIn("统计功效有限", basis)

    def test_manual_pack_and_import_share_schema(self):
        artifacts = ROOT / "tests" / ".artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        # 保留临时测试包，不使用 TemporaryDirectory 的永久删除清理。
        tmp = tempfile.mkdtemp(dir=artifacts)
        pack = Path(tmp) / "pack"
        rsi_loop.export_manual(pack, smoke=True)
        self.assertEqual(3, len(list((pack / "cases").glob("*.md"))))
        for path in pack.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("derived_from", text)
                self.assertNotIn("adjustment_note", text)
                self.assertNotIn("全部落在A的权利要求", text)
        response = rsi_loop.mock_judgment(FINAL_DOC, "CN106757342A")
        (pack / "responses" / "CN106757342A.json").write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
        imported, duplicates = rsi_loop.load_imported(pack / "responses")
        self.assertEqual({}, duplicates)
        self.assertIn("CN106757342A", imported)
        parsed = rsi_loop.parse_json_loose(imported["CN106757342A"])
        _judgment, errors = validate_judgment(parsed, rsi_loop.public_candidate("CN106757342A"), rsi_loop.TARGET)
        self.assertEqual([], errors)

    def test_local_endpoint_rules_and_secret_not_in_manifest_endpoint(self):
        env = {"LLM_BASE_URL": "http://127.0.0.1:8317/v1", "LLM_MODEL": "local-test", "LLM_API_KEY": "not-logged"}
        with patch.dict(os.environ, env, clear=True):
            config = rsi_loop.llm_config("local")
            self.assertEqual("http://127.0.0.1:8317/v1", config["endpoint"])
            self.assertNotIn("not-logged", config["endpoint"])
        env["LLM_BASE_URL"] = "https://example.com/v1"
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                rsi_loop.llm_config("local")


if __name__ == "__main__":
    unittest.main()
