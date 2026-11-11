"""宿主 Agent 复核入口：不调用外部模型，仍按模型复核契约校验引文和上下文。"""
import json
import tempfile
import unittest
from pathlib import Path

from modules.novelty_models import PATSNAP_SOURCE_SCOPE, Feature, PatentDocument, SourceSection
from modules.novelty_review import host_agent_review
from modules.patent_context import context_for
from modules.novelty_search import search_input_hash
from tools import run_host_agent


def make_document():
    return PatentDocument(
        "CN123A", "一种压力传感器", "https://analytics.zhihuiya.com/patent-view/abst?patentId=11111111-1111-1111-1111-111111111111",
        "2020-01-01",
        (SourceSection("s1", "一种压力传感器，包括壳体。", "说明书[0002]", "p1"),
         SourceSection("s2", "壳体内部设有电路板。", "说明书[0003]", "p2")),
        source_kind="patsnap_mcp_snapshot", complete=True, snapshot_schema_version=2,
        parsed_complete=True)


def make_features():
    return [Feature("F1", "一种压力传感器"), Feature("F2", "壳体内设电路板")]


def make_review():
    return {
        "document_id": "CN123A",
        "rows": [
            {"feature_id": "F1", "status": "disclosed", "section_id": "s1",
             "quote": "一种压力传感器", "scheme_id": "S1", "basis": "直接记载"},
            {"feature_id": "F2", "status": "not_found", "section_id": "", "quote": "",
             "scheme_id": "", "basis": "全文未记载"},
        ],
        "same_scheme_confirmed": True,
        "same_scheme_basis": "同一实施例",
        "scheme_section_ids": ["s1"],
        "enabling_disclosure_confirmed": True,
        "relevance": "related",
        "relevance_basis": "同一技术主题",
        "relevance_section_ids": ["s1"],
        "context_complete": True,
        "completeness_basis": "已读全部段落",
        "reviewed_section_ids": ["s1", "s2"],
    }


def make_package(features, document):
    return {
        "schema": "host-agent-package-v1",
        "created_at": "2026-11-10T00:00:00+00:00",
        "cutoff": "2024-01-01",
        "query": "",
        "material_original": "",
        "results_per_query": 5,
        "max_documents": 2,
        "features": [{"id": feature.id, "text": feature.text} for feature in features],
        # prepare 会按实际来源写入上下文；夹具省略它会让复核因来源范围不一致被拒。
        "patent_context": context_for(features, "2024-01-01", None, sources=PATSNAP_SOURCE_SCOPE),
        "search_audit": {
            "schema_version": 1,
            "input_hash": search_input_hash(features, "2024-01-01"),
            "mode": "provided_snapshots",
            "provider": "patsnap",
            "queries": [],
            "candidates": [],
        },
        "documents": [document.to_dict()],
        "errors": [],
        "baseline_decision": None,
    }


class HostAgentReviewTests(unittest.TestCase):
    def test_accepts_valid_review_and_marks_model_source(self):
        review = host_agent_review(make_review(), make_features(), make_document(), "2024-01-01")
        self.assertTrue(review.reviewed)
        self.assertEqual(review.review_method, "model")
        self.assertEqual(review.review_model, "host-agent")
        self.assertTrue(review.input_hash)
        self.assertTrue(review.review_context_hash)

    def test_rejects_quote_that_is_not_in_the_snapshot(self):
        data = make_review()
        data["rows"][0]["quote"] = "一种压力传感器，包括外壳"
        with self.assertRaises(ValueError):
            host_agent_review(data, make_features(), make_document(), "2024-01-01")

    def test_rejects_incomplete_section_coverage(self):
        data = make_review()
        data["reviewed_section_ids"] = ["s1"]
        with self.assertRaises(ValueError):
            host_agent_review(data, make_features(), make_document(), "2024-01-01")


class HostAgentFinishTests(unittest.TestCase):
    def test_finish_writes_report_without_network(self):
        features, document = make_features(), make_document()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / "package.json"
            reviews = root / "reviews.json"
            package.write_text(json.dumps(make_package(features, document), ensure_ascii=False), encoding="utf-8")
            reviews.write_text(json.dumps([make_review()], ensure_ascii=False), encoding="utf-8")
            args = type("Args", (), {"package": package, "reviews": reviews, "out": root / "out"})()
            self.assertEqual(run_host_agent.finish(args), 0)
            self.assertTrue((root / "out" / "report.md").read_text(encoding="utf-8").strip())
            state = json.loads((root / "out" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["reviews"][0]["review_model"], "lingxi-host-agent")
            self.assertEqual(state["documents"][0]["id"], "CN123A")
            # 有效复核必须真正进入判断，而不是被语境绑定拒绝后仍写出报告。
            self.assertEqual(state["decision"]["label"], "no_complete_disclosure_found")
            self.assertFalse(any("未绑定" in warning for warning in state["decision"]["warnings"]))


if __name__ == "__main__":
    unittest.main()
