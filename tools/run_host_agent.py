"""Run the precheck with the host agent doing the semantic review; no external LLM request.

The fixed stages stay in Python and the semantic judgment stays in the conversation:

    python tools/run_host_agent.py prepare --feature-text "压力传感器" --query "TACD:(压力传感器)" \
        --cutoff 2026-11-09 --out .local/cases/pressure

    # 宿主 Agent 读取 <out>/package.json 的原文段落，写出 <out>/reviews.json
    python tools/run_host_agent.py finish --package <out>/package.json \
        --reviews <out>/reviews.json --out <out>

`prepare` 只做在线检索并保存快照；`finish` 用已保存的快照和宿主复核重跑固定阶段，
不访问网络、不调用外部模型。复核记录仍标记为模型来源（宿主 Agent），不冒称人工确认。
"""
import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from modules.harness import NoveltyHarness
from modules.novelty_models import Feature, PatentDocument, check_cutoff
from modules.novelty_review import host_agent_review

HOST_MODEL = "lingxi-host-agent"
HOST_ENDPOINT = "host-agent://conversation"


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _template(features, documents) -> list:
    return [{
        "document_id": document.id,
        "rows": [{"feature_id": feature.id, "status": "", "section_id": "", "quote": "",
                  "scheme_id": "", "basis": ""} for feature in features],
        "same_scheme_confirmed": False,
        "same_scheme_basis": "",
        "scheme_section_ids": [],
        "enabling_disclosure_confirmed": False,
        "relevance": "",
        "relevance_basis": "",
        "relevance_section_ids": [],
        "context_complete": False,
        "completeness_basis": "",
        "reviewed_section_ids": [],
    } for document in documents]


def prepare(args) -> int:
    check_cutoff(args.cutoff)
    if args.out.exists():
        parser_error = f"输出目录已存在，请使用新目录：{args.out}"
        print(parser_error, file=sys.stderr)
        return 2
    feature_text = args.feature_text or ""
    if args.features_file:
        feature_text = Path(args.features_file).read_text(encoding="utf-8")
    if not feature_text.strip():
        print("缺少技术要素：--feature-text 或 --features-file 至少提供一项。", file=sys.stderr)
        return 2
    harness = NoveltyHarness(search_provider="patsnap", results_per_query=args.results_per_query,
                             max_documents=args.max_documents)
    state = harness.run(feature_text=feature_text, query=args.query or "", cutoff=args.cutoff,
                        material_original=args.material_original or "")
    features = state["features"]
    documents = state.get("documents") or []
    args.out.mkdir(parents=True, exist_ok=False)
    package = {
        "schema": "host-agent-package-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "cutoff": args.cutoff,
        "query": args.query or "",
        "material_original": args.material_original or "",
        "results_per_query": args.results_per_query,
        "max_documents": args.max_documents,
        "features": [{"id": feature.id, "text": feature.text} for feature in features],
        "patent_context": state.get("patent_context"),
        "search_audit": state.get("search_audit"),
        "documents": [document.to_dict() for document in documents],
        "errors": state.get("errors") or [],
        "baseline_decision": asdict(state["decision"]) if state.get("decision") else None,
    }
    _write(args.out / "package.json", package)
    _write(args.out / "review-template.json", _template(features, documents))
    audit = state.get("search_audit") or {}
    summary = {
        "output_dir": str(args.out.resolve()),
        "features": len(features),
        "provider": audit.get("provider"),
        "queries": [item.get("query") for item in audit.get("queries", [])],
        "total_matches": [item.get("total_matches") for item in audit.get("queries", [])],
        "documents": [{"id": document.id, "publication_date": document.publication_date,
                       "sections": len(document.sections),
                       "characters": sum(len(section.text) for section in document.sections)}
                      for document in documents],
        "baseline_decision": package["baseline_decision"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n下一步：读取 {args.out / 'package.json'} 的 documents，按 review-template.json 写出 reviews.json，再执行 finish。")
    return 0


def finish(args) -> int:
    package = json.loads(Path(args.package).read_text(encoding="utf-8"))
    if package.get("schema") != "host-agent-package-v1":
        print("不是宿主 Agent 运行包。", file=sys.stderr)
        return 2
    features = [Feature(item["id"], item["text"]) for item in package["features"]]
    documents = [PatentDocument.from_dict(item) for item in package["documents"]]
    if not documents:
        print("运行包中没有已取得正文的文献，未生成结论。", file=sys.stderr)
        return 2
    by_id = {document.id: document for document in documents}
    raw = json.loads(Path(args.reviews).read_text(encoding="utf-8"))
    raw = raw.get("reviews") if isinstance(raw, dict) else raw
    if not isinstance(raw, list) or not raw:
        print("reviews.json 缺少复核数组。", file=sys.stderr)
        return 2
    cutoff = package["cutoff"]
    reviews = []
    for item in raw:
        document = by_id.get(item.get("document_id"))
        if document is None:
            print(f"复核引用了运行包之外的文献：{item.get('document_id')}", file=sys.stderr)
            return 2
        reviews.append(host_agent_review(item, features, document, cutoff,
                                         model=HOST_MODEL, endpoint=HOST_ENDPOINT,
                                         patent_context=package.get("patent_context")))
    harness = NoveltyHarness(search_provider="patsnap",
                             results_per_query=package["results_per_query"],
                             max_documents=package["max_documents"])
    state = harness.run(features=features, documents=documents, reviews=reviews, cutoff=cutoff,
                        search_audit=package["search_audit"], query=package.get("query", ""),
                        material_original=package.get("material_original", ""))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.md").write_text(state.get("markdown") or "", encoding="utf-8")
    _write(args.out / "state.json", {
        "features": [asdict(feature) for feature in features],
        "documents": [document.to_dict() for document in documents],
        "reviews": [asdict(review) for review in reviews],
        "search_audit": state.get("search_audit"),
        "decision": asdict(state["decision"]) if state.get("decision") else None,
        "report_payload": state.get("report_payload"),
        "skill_records": state.get("skill_records"),
        "trace": state.get("trace"),
    })
    decision = state.get("decision")
    print(json.dumps({
        "output_dir": str(args.out.resolve()),
        "label": getattr(decision, "label", None),
        "text": getattr(decision, "text", None),
        "reasons": getattr(decision, "reasons", []),
        "warnings": getattr(decision, "warnings", []),
        "documents": len(documents),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare", help="在线检索并保存原文快照")
    prep.add_argument("--feature-text", default="", help="已确认技术要素，按行分隔")
    prep.add_argument("--features-file", default="", help="从文本文件读取技术要素")
    prep.add_argument("--query", default="", help="自定义智慧芽检索式；留空时按要素生成")
    prep.add_argument("--cutoff", required=True, help="判断基准日 YYYY-MM-DD")
    prep.add_argument("--material-original", default="", help="可选：材料原文，供上下文记录")
    prep.add_argument("--results-per-query", type=int, default=5, help="每条查询保留候选数 1–25")
    prep.add_argument("--max-documents", type=int, default=2, help="最多取得正文的文献数")
    prep.add_argument("--out", type=Path, required=True, help="新的运行目录")
    prep.set_defaults(handler=prepare)

    fin = sub.add_parser("finish", help="使用宿主的 reviews.json 生成报告，不访问网络")
    fin.add_argument("--package", type=Path, required=True)
    fin.add_argument("--reviews", type=Path, required=True)
    fin.add_argument("--out", type=Path, required=True)
    fin.set_defaults(handler=finish)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
