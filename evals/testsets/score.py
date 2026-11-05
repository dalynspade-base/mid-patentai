# -*- coding: utf-8 -*-
"""对账打分器（audit）：像 rsi-lab 的 audit.py 一样，用细则答案对被测对象对账。

用法（在仓库根目录，使用项目解释器）：
    .\\.venv-novelty\\Scripts\\python.exe -X utf8 evals\\testsets\\score.py                 # 跑两套
    .\\.venv-novelty\\Scripts\\python.exe -X utf8 evals\\testsets\\score.py --set theory    # 只跑理论集
    .\\.venv-novelty\\Scripts\\python.exe -X utf8 evals\\testsets\\score.py --set engineering
    ... score.py --split anchor      # 只看锚集（rsi-lab：锚集从不参与迭代决策，只对账）
    ... score.py --case T05          # 单查一个案例
    ... score.py --json out.json     # 另存机器可读结果

- 理论集：对冻结的候选输出做机械对账（引文逐字、小节映射、逐项状态、业务结论、
  自报裁决、必提事项），并打印细则裁决与六维违反项。机械对账覆盖不到的语义问题
  （如工序顺序丢失）如实标注"机械未覆盖，按细则裁决"。
- 工程集：把构造输入喂给项目真实规则引擎 modules.novelty_rules.assess_novelty，
  与细则答案对账；契约用例直接调用数据契约函数。不调用模型、不联网。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

sys.path.insert(0, str(REPO_ROOT))

from policy import DIMENSIONS, normalize  # noqa: E402  (policy.py 同目录)


def load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


CORPUS = load("patents.json")["patents"]


def section_text(section_id: str) -> str:
    pub, _tag = section_id.split(":", 1)
    return CORPUS[pub]["sections"][section_id]["text"]


# ---------------------------------------------------------------- 理论集

def score_theory_case(case: dict) -> dict:
    label = case["label"]
    cand = case["candidate_output"]
    rows = cand.get("evidence", [])
    by_feature = {r["feature_id"]: r for r in rows}

    checks: dict[str, list[str]] = {"quote_verbatim": [], "section_map": [],
                                    "statuses": [], "conclusion": [], "verdict": [],
                                    "required_mentions": []}

    # R01 引文逐字回查
    for r in rows:
        if r.get("status") != "disclosed":
            continue
        sid, quote = r.get("section_id", ""), r.get("quote", "")
        if not sid or not quote or sid not in case_documents(case) or \
                normalize(quote) not in normalize(section_text(sid)):
            checks["quote_verbatim"].append(r["feature_id"])

    # R08 小节映射
    for fid, expect_sid in (label.get("expected_sections") or {}).items():
        if expect_sid is None:
            continue
        row = by_feature.get(fid)
        if row is None or row.get("section_id") != expect_sid:
            checks["section_map"].append(fid)

    # R02 逐项状态
    for fid, expect_status in (label.get("expected_statuses") or {}).items():
        row = by_feature.get(fid)
        if row is None or row.get("status") != expect_status:
            checks["statuses"].append(fid)

    # 业务结论
    if cand.get("conclusion") != label.get("expected_conclusion"):
        checks["conclusion"].append(cand.get("conclusion"))

    # 自报裁决 vs 细则裁决（R14）
    self_verdict = cand.get("self_verdict")
    verdict_row = {"self": self_verdict, "gold": label.get("expected_verdict")}
    if self_verdict is not None and self_verdict != label.get("expected_verdict"):
        checks["verdict"].append(self_verdict)

    # 必提事项（如 T15 必须提到同族 A 的公开日）
    blob = " ".join(str(cand.get(k, "")) for k in ("notes", "same_scheme_basis", "next_action"))
    for token in label.get("required_mentions", []):
        if token not in blob:
            checks["required_mentions"].append(token)

    mechanical_failed = any(checks[k] for k in
                            ("quote_verbatim", "section_map", "statuses", "conclusion",
                             "verdict", "required_mentions"))
    # 语义层的实质捕获只看内容性检查；自报裁决不一致只说明候选“自己答错了”，
    # 不算机械对账发现了那个语义问题。
    solid_failed = any(checks[k] for k in
                       ("quote_verbatim", "section_map", "statuses", "conclusion",
                        "required_mentions"))
    if label.get("expected_verdict") == "pending_adjudication":
        coverage = "待裁决示范：诚实标注 unknown，无违反项"
    elif label.get("expected_verdict") == "needs_revision" and not solid_failed:
        coverage = "机械未覆盖语义层，按细则裁决（交独立裁判/人工复核）"
    elif solid_failed:
        coverage = "机械对账已捕获"
    else:
        coverage = "机械对账与细则一致"

    return {"id": case["id"], "usage": case["usage"], "patents": case["patents"],
            "trap": case["trap"], "checks": checks, "verdict_row": verdict_row,
            "gold_conclusion": label.get("expected_conclusion"),
            "cand_conclusion": cand.get("conclusion"),
            "violations": [f"{DIMENSIONS[v['dimension']]}：{v['issue']}"
                           for v in label.get("violations", [])],
            "coverage": coverage}


def case_documents(case: dict) -> set[str]:
    return {sid for doc in case.get("documents", []) for sid in doc.get("section_ids", [])}


def run_theory(cases: list[dict], split: str) -> tuple[list[dict], dict]:
    results = []
    for case in cases:
        if split != "all" and case["usage"] != split:
            continue
        results.append(score_theory_case(case))

    n = len(results)
    quote_clean = sum(1 for r in results if not r["checks"]["quote_verbatim"])
    conclusion_ok = sum(1 for r in results if not r["checks"]["conclusion"])
    status_ok = sum(1 for r in results if not r["checks"]["statuses"])
    verdict_ok = sum(1 for r in results if not r["checks"]["verdict"])
    revision_cases = sum(1 for r in results if r["verdict_row"]["gold"] == "needs_revision")
    caught = sum(1 for r in results if r["verdict_row"]["gold"] == "needs_revision"
                 and any(r["checks"][k] for k in ("quote_verbatim", "section_map", "statuses",
                                                  "conclusion", "required_mentions")))
    semantic_only_cases = [r["id"] for r in results
                           if r["verdict_row"]["gold"] == "needs_revision"
                           and not any(r["checks"][k] for k in ("quote_verbatim", "section_map",
                                                                "statuses", "conclusion",
                                                                "required_mentions"))]
    dim_count: dict[str, int] = {}
    for r in results:
        for v in r["violations"]:
            dim_count[v.split("：")[0]] = dim_count.get(v.split("：")[0], 0) + 1

    summary = {
        "案例数": n,
        "引文逐字通过": f"{quote_clean}/{n}",
        "业务结论与细则一致": f"{conclusion_ok}/{n}",
        "逐项状态与细则一致": f"{status_ok}/{n}",
        "自报裁决与细则一致": f"{verdict_ok}/{n}",
        "需修订案例": revision_cases,
        "其中机械对账可捕获": caught,
        "需独立裁判的语义案例": semantic_only_cases,
        "六维违反分布": dim_count,
    }
    return results, summary


# ---------------------------------------------------------------- 工程集

def build_models(case: dict):
    from modules.novelty_models import (EvidenceRow, Feature, PatentDocument, DocumentReview,
                                        review_input_hash, replace)
    feats = [Feature(f"F{i}", text) for i, text in enumerate(case["features"], 1)]
    docs, sections_by_doc = [], {}
    for raw in case["documents"]:
        pub = raw["id"]
        meta = CORPUS[pub]
        section_ids = raw["section_refs"]
        secs = tuple(__import__("modules.novelty_models", fromlist=["SourceSection"]).SourceSection(
            sid, section_text(sid)) for sid in section_ids)
        doc = PatentDocument(
            id=pub, title=meta["title"], url=meta["url"],
            publication_date=raw.get("publication_date", meta["publication_date"]),
            filing_date=raw.get("filing_date", meta.get("filing_date", "")),
            priority_date=raw.get("priority_date", meta.get("priority_date", "")),
            sections=secs, complete=raw.get("complete", True),
            source_kind="manual_google_patents_snapshot",
        )
        docs.append(doc)
        sections_by_doc[pub] = section_ids

    reviews = []
    rv = case.get("review")
    if rv:
        rows = tuple(EvidenceRow(**r) for r in rv["rows"])
        review = DocumentReview(
            document_id=rv["document_id"], rows=rows, reviewed=True,
            same_scheme_confirmed=rv.get("same_scheme_confirmed", False),
            enabling_disclosure_confirmed=rv.get("enabling_disclosure_confirmed", False),
            reviewer=rv.get("reviewer", "testset-fixture-reviewer"),
            reviewed_at=rv.get("reviewed_at", "2026-11-04T00:00:00+00:00"),
            same_scheme_basis=rv.get("same_scheme_basis", ""),
            scheme_section_ids=tuple(rv.get("scheme_section_ids", ())),
            relevance=rv.get("relevance", "related"),
            relevance_basis=rv.get("relevance_basis", ""),
            relevance_section_ids=tuple(rv.get("relevance_section_ids", ())),
            review_method=rv.get("review_method", "human"),
            review_model=rv.get("review_model", ""),
            review_endpoint=rv.get("review_endpoint", ""),
            review_prompt_version=rv.get("review_prompt_version", ""),
            context_complete=rv.get("context_complete", False),
            completeness_basis=rv.get("completeness_basis", ""),
            reviewed_section_ids=tuple(sections_by_doc.get(rv["document_id"], ())),
        )
        doc = next(d for d in docs if d.id == rv["document_id"])
        review = replace(review, input_hash=review_input_hash(feats, doc, case["cutoff"], review))
        reviews.append(review)
    return feats, docs, reviews


def run_engineering(cases: list[dict], split: str) -> tuple[list[dict], dict]:
    from modules.novelty_models import (EvidenceRow, Feature, PatentDocument, SourceSection)
    from modules.novelty_rules import assess_novelty
    from modules.novelty_models import google_patent_url, parse_features

    results = []
    for case in cases:
        if split != "all" and case["usage"] != split:
            continue
        if case["kind"] == "decision":
            feats, docs, reviews = build_models(case)
            decision = assess_novelty(feats, docs, reviews, case["cutoff"],
                                      errors=case.get("errors"))
            ok = decision.label == case["expected"]["conclusion"]
            results.append({"id": case["id"], "usage": case["usage"], "kind": "decision",
                            "scenario": case["scenario"], "gold": case["expected"]["conclusion"],
                            "system": decision.label, "match": ok,
                            "warnings": list(decision.warnings)[:3]})
        elif case["check"] == "google_patent_url":
            items = []
            for item in case["items"]:
                try:
                    google_patent_url(item["input"])
                    got = "ok"
                except ValueError:
                    got = "error"
                items.append({"input": item["input"], "expect": item["expect"], "got": got,
                              "match": got == item["expect"]})
            results.append({"id": case["id"], "usage": case["usage"], "kind": "contract",
                            "scenario": case["scenario"], "items": items,
                            "match": all(i["match"] for i in items)})
        elif case["check"] == "parse_features":
            items = []
            for item in case["items"]:
                feats = parse_features(item["input"])
                got = f"count_{len(feats)}"
                items.append({"input": item["input"], "expect": item["expect"], "got": got,
                              "match": got == item["expect"]})
            results.append({"id": case["id"], "usage": case["usage"], "kind": "contract",
                            "scenario": case["scenario"], "items": items,
                            "match": all(i["match"] for i in items)})
        elif case["check"] == "snapshot_hash_binding":
            meta = CORPUS["US3664335A"]
            doc = PatentDocument(id="US3664335A", title=meta["title"], url=meta["url"],
                                 publication_date=meta["publication_date"],
                                 filing_date=meta["filing_date"],
                                 sections=(SourceSection("US3664335A:claim1",
                                                         section_text("US3664335A:claim1")),),
                                 complete=True, source_kind="manual_google_patents_snapshot")
            good = doc.to_dict()
            good["snapshot_hash"] = doc.snapshot_hash
            bad = dict(good)
            bad["snapshot_hash"] = "0" * 64
            ok_ok = error_ok = False
            try:
                PatentDocument.from_dict(good)
                ok_ok = True
            except ValueError:
                pass
            try:
                PatentDocument.from_dict(bad)
            except ValueError:
                error_ok = True
            results.append({"id": case["id"], "usage": case["usage"], "kind": "contract",
                            "scenario": case["scenario"],
                            "items": [{"check": "correct_hash_passes", "match": ok_ok},
                                      {"check": "tampered_hash_rejected", "match": error_ok}],
                            "match": ok_ok and error_ok})
        elif case["check"] == "snapshot_schema_version":
            items = []
            for item in case["items"]:
                raw = item["input"]
                try:
                    PatentDocument(id="US3664335A", title="t", url=meta_url(), publication_date="2020-01-01",
                                   sections=tuple(SourceSection(**s) for s in raw["sections"]),
                                   snapshot_schema_version=raw["snapshot_schema_version"])
                    got = "ok"
                except ValueError:
                    got = "error"
                items.append({"input": raw["snapshot_schema_version"], "expect": item["expect"],
                              "got": got, "match": got == item["expect"]})
            results.append({"id": case["id"], "usage": case["usage"], "kind": "contract",
                            "scenario": case["scenario"], "items": items,
                            "match": all(i["match"] for i in items)})
    matched = sum(1 for r in results if r["match"])
    summary = {"用例数": len(results), "与细则一致": f"{matched}/{len(results)}",
               "决策用例": sum(1 for r in results if r["kind"] == "decision"),
               "契约用例": sum(1 for r in results if r["kind"] == "contract")}
    return results, summary


def meta_url() -> str:
    return CORPUS["US3664335A"]["url"]


# ---------------------------------------------------------------- 主流程

def main() -> int:
    parser = argparse.ArgumentParser(description="专利语义 test 集对账打分器")
    parser.add_argument("--set", choices=["theory", "engineering", "all"], default="all")
    parser.add_argument("--split", choices=["train", "anchor", "all"], default="all")
    parser.add_argument("--case", default="", help="只看某个案例 id")
    parser.add_argument("--json", default="", help="把结果另存为 JSON 文件")
    args = parser.parse_args()

    theory_cases = load("theory_cases.json")["cases"]
    eng_cases = load("engineering_cases.json")["cases"]
    if args.case:
        theory_cases = [c for c in theory_cases if c["id"] == args.case]
        eng_cases = [c for c in eng_cases if c["id"] == args.case]

    out: dict = {}
    exit_code = 0

    if args.set in ("theory", "all") and theory_cases:
        results, summary = run_theory(theory_cases, args.split)
        out["theory"] = {"summary": summary, "results": results}
        print("=" * 72)
        print("理论集（六维语义 test）：候选输出 vs 细则答案")
        print("=" * 72)
        for r in results:
            failed = [k for k, v in r["checks"].items() if v]
            flag = "；".join(f"{k}:{','.join(v)}" for k, v in r["checks"].items() if v) or "全部一致"
            print(f"[{r['id']}] ({r['usage']}) {r['patents']} {r['trap']}")
            print(f"    检查：{flag}")
            print(f"    裁决：候选自报 {r['verdict_row']['self']} / 细则 {r['verdict_row']['gold']}"
                  f" ｜ 结论：候选 {r['cand_conclusion']} / 细则 {r['gold_conclusion']} ｜ {r['coverage']}")
            for v in r["violations"]:
                print(f"      - {v}")
        print("-" * 72)
        print("理论集汇总：", json.dumps(summary, ensure_ascii=False))

    if args.set in ("engineering", "all") and eng_cases:
        results, summary = run_engineering(eng_cases, args.split)
        out["engineering"] = {"summary": summary, "results": results}
        print("=" * 72)
        print("工程集（决策规则 + 数据契约）：规则引擎/契约 vs 细则答案")
        print("=" * 72)
        for r in results:
            mark = "OK " if r["match"] else "MISMATCH"
            print(f"[{mark}] {r['id']} ({r['usage']}) {r['scenario']}")
            if r["kind"] == "decision":
                print(f"    细则 {r['gold']} ｜ 引擎 {r['system']}")
                for w in r["warnings"]:
                    print(f"    warning: {w}")
            else:
                for i in r["items"]:
                    print(f"    {json.dumps(i, ensure_ascii=False)}")
        print("-" * 72)
        print("工程集汇总：", json.dumps(summary, ensure_ascii=False))
        mismatches = [r["id"] for r in results if not r["match"]]
        if mismatches:
            exit_code = 1
            print(f"!! 与细则不一致：{', '.join(mismatches)}（label 或引擎其一发生了变化，需人工核查）")

    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已保存：{args.json}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
