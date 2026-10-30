"""历史DeepEval工程契约兼容；主语义验收见modules.semantic_eval。

基础 eval 不使用 LLM 裁判、不需要 API Key、不上传云端。合成题用于回归，不能
作为真实专利准确率。真实金标准应另由专业人员标注并隔离盲测集。
"""

from __future__ import annotations

import importlib.metadata
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone

# 在导入框架前关闭框架遥测和自动环境文件读取，离线基础 eval 不读取用户密钥。
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
os.environ["DEEPEVAL_DISABLE_DOTENV"] = "1"

from deepeval.metrics import BaseMetric
from deepeval.test_case import LLMTestCase

from evals.legacy.fixtures import case_inputs, dataset
from modules.novelty_matching import SynonymLexicon
from modules.novelty_models import LABELS, PatentDocument, SourceSection
from modules.harness import NoveltyHarness
from modules.evidence_retriever import PatentRetriever


class NoveltyContractMetric(BaseMetric):
    """比较显式教学参考标签和引用；不是外部独立的专业专利标注。"""

    def __init__(self) -> None:
        self.threshold = 1.0
        self.async_mode = False
        self.strict_mode = True
        self.include_reason = True
        self.score = 0.0
        self.success = False
        self.reason = "尚未评测"

    @property
    def __name__(self) -> str:
        return "NoveltyContract"

    def measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        try:
            actual = json.loads(test_case.actual_output)
            expected = json.loads(test_case.expected_output)
            label_ok = actual["label"] == expected["label"]
            citations_ok = set(expected.get("required_documents", [])) <= set(actual.get("document_ids", []))
            # 风险标签不能没有定位到具体文献，也不能生成“可申请/不侵权”等越界标签。
            contract_ok = actual["label"] in LABELS and (
                actual["label"] != "potential_novelty_risk" or bool(actual.get("document_ids")))
            self.success = label_ok and citations_ok and contract_ok
            self.reason = f"标签匹配={label_ok}；引用要求={citations_ok}；风险依据={contract_ok}"
        except (TypeError, KeyError, ValueError):
            self.success = False
            self.reason = "待测结果或参考标签格式无效。"
        self.score = 1.0 if self.success else 0.0
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args, **kwargs) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return self.success


def run_basic_evals() -> dict:
    """一次执行三个 skill 的独立检查，并保留逐条结果，不以平均分掩盖关键失败。"""
    data = dataset()
    results: list[dict] = []
    harness = NoveltyHarness(allow_synthetic=True)
    for case in data["cases"]:
        state = harness.run(**case_inputs(case, data["cutoff"]))
        expected = {"label": case["expected"]}
        if case["id"] == "single_document_all_features":
            expected["required_documents"] = ["D1"]
        metric = NoveltyContractMetric()
        score = metric.measure(LLMTestCase(
            input=json.dumps({"case": case["id"], "features": case["features"]}, ensure_ascii=False),
            actual_output=json.dumps(asdict(state["decision"]), ensure_ascii=False),
            expected_output=json.dumps(expected),
        ))
        results.append({"skill": "langgraph", "id": case["id"], "passed": score == 1,
                        "expected": case["expected"], "actual": state["decision"].label,
                        "trace": state["trace"], "reason": metric.reason})
    for case in data["synonym_cases"]:
        docs = [
            PatentDocument("TARGET", "合成同义词目标", "https://patents.google.com/patent/TARGET", "2023-01-01",
                           (SourceSection("s1", case["target"]),), source_kind="synthetic_eval", complete=True),
            PatentDocument("NOISE", "合成干扰文献", "https://patents.google.com/patent/NOISE", "2023-01-01",
                           (SourceSection("s1", "一种用于烘焙食品的面粉配方。"),), source_kind="synthetic_eval", complete=True),
        ]
        hits = PatentRetriever(docs, allow_synthetic=True).retrieve(case["query"], top_k=1)
        passed = bool(hits and hits[0].document_id == "TARGET" and hits[0].text == case["target"])
        results.append({"skill": "llamaindex", "id": case["id"], "passed": passed,
                        "actual": hits[0].document_id if hits else None})
    lexicon = SynonymLexicon()
    for name, left, right in [
        ("keep_negation", "包括控制器", "不包括控制器"),
        ("keep_numeric_limit", "10 kPa", "100 kPa"),
        ("keep_connection_relation", "串联", "并联"),
        ("avoid_broad_equivalence", "温度传感器", "压力传感器"),
    ]:
        results.append({"skill": "llamaindex", "id": name,
                        "passed": lexicon.canonicalize(left) != lexicon.canonicalize(right)})
    # 裁判自身的正/负/损坏输入也必须有 eval，证明它确实能拒绝错误答案。
    checks = [
        ("judge_accepts_correct", '{"label":"insufficient_evidence"}', '{"label":"insufficient_evidence"}', 1),
        ("judge_rejects_wrong", '{"label":"no_complete_disclosure_found"}', '{"label":"insufficient_evidence"}', 0),
        ("judge_requires_risk_citation", '{"label":"potential_novelty_risk","document_ids":[]}', '{"label":"potential_novelty_risk"}', 0),
        ("judge_rejects_invalid_json", 'not json', '{"label":"insufficient_evidence"}', 0),
    ]
    for name, actual, expected, target_score in checks:
        score = NoveltyContractMetric().measure(LLMTestCase(input=name, actual_output=actual, expected_output=expected))
        results.append({"skill": "deepeval", "id": name, "passed": score == target_score, "score": score})
    summary = {skill: {"passed": sum(row["passed"] for row in results if row["skill"] == skill),
                       "total": sum(row["skill"] == skill for row in results)}
               for skill in ("langgraph", "llamaindex", "deepeval")}
    return {"dataset_version": data["dataset_version"], "provenance": data["provenance"],
            "evaluation_kind": "synthetic_rule_regression",
            "reference_provenance": "author_written_teaching_reference_not_independent_patent_labels",
            "fixture_contract_version": 3,
            "review_provenance": "合成夹具模拟的复核字段，不是真实人员提交记录。",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "versions": {package: importlib.metadata.version(package)
                         for package in ("langgraph", "llama-index-core", "deepeval")},
            "all_passed": all(row["passed"] for row in results), "summary": summary, "cases": results,
            "limitations": ["合成回归案例，不是实际专利准确率。", "同义词词表有限，未知术语需补充复核。"]}
