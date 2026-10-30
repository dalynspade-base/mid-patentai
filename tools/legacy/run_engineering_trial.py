"""真实工程专利单点随机修改试运行。API Key 只通过隐藏输入进入内存。

原版与变更版共用 Google Patents 原文快照，调用真实模型提出对照，再运行
现有 Harness。模型建议不会被伪装成人工复核；报告如实保留证据不足。
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import random
import re
import secrets
import sys
import time
import warnings
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import requests


def format_markdown(lines):
    """段落留空行，但表格行必须连续，否则 GitHub 不会把它渲染成表格。"""
    text = "\n\n".join(lines)
    text = re.sub(r"(?m)(^\|[^\n]*\|)\n(?:[ \t]*\n)+(?=\|)", r"\1\n", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--case", type=Path)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--model", default="deepseek-flash")
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("输出目录已存在，请选一个新的运行目录；不覆盖历史结果。")
    sys.path.insert(0, str(args.project_root.resolve()))
    from modules.llm_client import LLMClient
    from modules.novelty_matching import SynonymLexicon
    from modules.novelty_models import Feature, PatentDocument
    from modules.novelty_report import to_markdown
    from modules.patent_tools import build_report as report_payload
    from modules.patent_search import search_patents
    from modules.harness import NoveltyHarness
    from modules.harness import model_suggest_rows
    from engineering.legacy.basic_contracts import run_basic_evals

    case_path = args.case or args.project_root / "examples" / "engineering_trial.json"
    case = json.loads(case_path.read_text(encoding="utf-8"))
    seed = args.seed if args.seed is not None else secrets.randbits(32)
    mutation = random.Random(seed).choice(case["mutations"])
    original = [Feature(item["id"], item["text"]) for item in case["features"]]
    modified = [replace(feature, text=mutation["text"]) if feature.id == mutation["feature_id"] else feature for feature in original]
    if sum(a.text != b.text for a, b in zip(original, modified)) != 1:
        raise ValueError("只允许修改一个要素。")
    documents = []
    for publication in (case["source_patent"], case["reference_patent"]):
        path = args.source_dir / f"{publication}.json"
        if not path.exists():
            path = args.source_dir / f"{publication}_source.json"
        documents.append(PatentDocument.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    if any(doc.source_kind != "google_patents_page" or not doc.sections for doc in documents):
        raise ValueError("本案例需要已真实获取的 Google Patents 原文快照。")
    # 非 TTY 或回退到可见输入时停止，不接受将密钥拼进命令行或写入配置文件。
    warnings.filterwarnings("error", category=getpass.GetPassWarning)
    key = getpass.getpass("DeepSeek API key (hidden; memory only): ").strip()
    if not key:
        raise ValueError("未取得 API Key。")

    def safe(value):
        text = json.dumps(value, ensure_ascii=False, indent=2)
        text = text.replace(key, "[REDACTED]")
        return re.sub(r"sk-[A-Za-z0-9_-]{16,}", "[REDACTED]", text)

    args.output_dir.mkdir(parents=True, exist_ok=False)
    model_calls = []

    def recording_post(url, **kwargs):
        if url != "https://api.deepseek.com/chat/completions":
            raise ValueError("此示例只向已配置的 DeepSeek 官方 API 发送凭据。")
        # DeepSeek 默认可启用思考；本例显式关闭以减少演示等待和不必要成本。
        kwargs["json"]["thinking"] = {"type": "disabled"}
        start = time.monotonic()
        response = requests.post(url, **kwargs)
        info = {"http_status": response.status_code, "elapsed_seconds": round(time.monotonic() - start, 2)}
        if response.ok:
            data = response.json()
            info.update({"response_id": data.get("id"), "model": data.get("model"), "usage": data.get("usage"),
                         "finish_reason": data.get("choices", [{}])[0].get("finish_reason")})
        model_calls.append(info)  # 只记状态和用量，不记录请求头、密钥或客户端对象。
        return response

    client = LLMClient(key, "https://api.deepseek.com", args.model, post=recording_post)
    lexicon = SynonymLexicon()
    harness = NoveltyHarness(lexicon=lexicon)
    states = {}
    print(f"Mutation selected: {mutation['id']}; feature={mutation['feature_id']}; seed={seed}", flush=True)
    search = {"query": "rotatable ballpoint pen " + mutation["id"].replace("_", " "), "ok": False}
    try:
        candidates = search_patents(search["query"], max_results=5)
        search.update({"ok": True, "candidates": candidates})
    except RuntimeError as exc:
        search["error"] = str(exc)
    # 真实检索探针与固定两篇文献的对照试验分开记录，不冒充全库检索覆盖。
    for name, features in (("original", original), ("modified", modified)):
        print(f"Running {name}: LlamaIndex retrieval and real model alignment...", flush=True)
        state = harness.run(features=features, cutoff=case["cutoff"], documents=documents)
        try:
            reviews = model_suggest_rows(features, documents, client, confirmed=True)
            state["suggestions"] = reviews
            state["model_status"] = "success"
        except (RuntimeError, ValueError) as exc:
            state["model_status"] = "failed"
            state["model_error"] = str(exc)
        states[name] = state
        payload = report_payload(state, f"旋转咔嗒圆珠笔：{name}", case["cutoff"], lexicon.groups)
        payload["model_status"] = state["model_status"]
        if "model_error" in state:
            payload["model_error"] = state["model_error"]
        (args.output_dir / f"{name}_evidence.json").write_text(safe(payload), encoding="utf-8")
        (args.output_dir / f"{name}_report.md").write_text(to_markdown(payload), encoding="utf-8")
    basic = run_basic_evals()
    metadata = {"created_at": datetime.now(timezone.utc).isoformat(), "seed": seed, "mutation": mutation,
                "changed_feature_count": 1, "source_urls": [doc.url for doc in documents],
                "model": args.model, "model_calls": model_calls, "search_probe": search,
                "decisions": {name: state["decision"].label for name, state in states.items()},
                "model_statuses": {name: state["model_status"] for name, state in states.items()},
                "human_review_performed": False, "basic_eval_summary": basic["summary"]}
    (args.output_dir / "run_metadata.json").write_text(safe(metadata), encoding="utf-8")
    for doc in documents:
        (args.output_dir / f"{doc.id}_source.json").write_text(safe(doc.to_dict() | {"snapshot_hash": doc.snapshot_hash}), encoding="utf-8")
    lines = ["# 工程专利单点随机修改：真实试运行记录", "",
             "## 示例与范围", f"基准专利：[US4968168A — Ballpoint pen with clicking]({case['source_url']})；公开日 1990-11-06。",
             f"补充对比文献：[US5944436A — Pen possible to sound]({case['reference_url']})；公开日 1999-08-31。",
             "通俗解释：转动笔帽，笔芯伸缩；弹性偏置使两个带齿的环形件配合，到位时发出咔嗒声。",
             "按整套选定技术要素对照，不展开独立/从属权利要求。此处是具体工程方案的教学试验，不是对该专利完整保护范围的重建。",
             f"本次假设判断基准日：{case['cutoff']}。专利是否过期不决定其公开内容能否成为现有技术。",
             "", "## 随机修改", f"随机种子：`{seed}`；候选变更数：{len(case['mutations'])}；选中：`{mutation['id']}`。",
             f"只修改 **{mutation['feature_id']}**：{mutation['zh']}", "",
             "| 要素 | 原方案 | 修改后 |", "|---|---|---|"]
    for feature in case["features"]:
        changed = feature["id"] == mutation["feature_id"]
        lines.append(f"| {feature['id']} | {feature['zh']} | {mutation['text'] if changed else '保持不变'} |")
    lines += ["", "## 实际运行链路", "Google Patents 真实页面 → 原文快照 → LlamaIndex 候选检索 → DeepSeek 真实对照 → 引文校验 → harness 调度规则判断 → Markdown/JSON。",
              "检索探针与固定两篇文献的前后对照分别记录；没有把两篇文献覆盖范围说成全库覆盖率。",
              f"在线检索探针：{'成功' if search['ok'] else '失败或不可用'}。详见 run_metadata.json。",
              "", "## 原方案与修改版结果", "| 版本 | 真实模型对照 | Harness 结果 |", "|---|---|---|"]
    for name, title in (("original", "原方案"), ("modified", "修改版")):
        state = states[name]
        lines.append(f"| {title} | {state['model_status']} | {state['decision'].text} |")
    lines += ["", "**没有专业人员人工复核，所以 reviewed 始终为 false。模型对照不冒充人工确认，系统的保守结果不是自动宣告可授权。**",
              "", "## 修改版逐文献对照（模型建议，待复核）"]
    for review in states["modified"].get("suggestions", []):
        lines += ["", f"### {review.document_id}", "| 要素 | 建议状态 | 原文位置 | 引文 |", "|---|---|---|---|"]
        for row in review.rows:
            quote = row.quote.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {row.feature_id} | {row.status} | {row.section_id} | {quote or '未取得'} |")
    if states["modified"]["model_status"] != "success":
        lines.append("模型调用或对照校验失败，未编造对照结果；错误类别见 JSON 记录。")
    lines += ["", "## 运行检查", "- 只改变一个技术要素，其他要素逐字保持不变。",
              "- 所有文献来自真实 Google Patents 页面，保存公开日期、段落与快照哈希。",
              "- 模型标为已披露的引文，必须通过现有代码的逐字回查。",
              "- 三个 skill 的教学基础 eval 已重跑，见下表；它不代表本次真实专利判断正确率。",
              "", "| Skill | 基础 eval |", "|---|---|"]
    lines += [f"| {name} | {value['passed']}/{value['total']} |" for name, value in basic["summary"].items()]
    lines += ["", "## 复核与复现", "- 由专业人员确认逐项披露状态、同义表述、同一技术方案及充分公开，再提交正式复核。",
              "- 对修改点补查非专利文献、更多专利与相关日期；未发现不等于不存在。",
              "- random seed 仅复现修改点选择，模型回答可能波动；本次原始对照和运行用量保存在 JSON。",
              "- API Key 只在内存中使用，不写入文件、输出、Git 配置或历史。"]
    report = format_markdown(lines)
    if key in report:
        raise RuntimeError("输出中检测到凭据，停止保存。")
    (args.output_dir / "engineering_trial.md").write_text(report, encoding="utf-8")
    all_models_ok = all(state["model_status"] == "success" for state in states.values())
    print(safe({"report": str(args.output_dir / "engineering_trial.md"), "mutation": mutation["id"],
                "all_models_succeeded": all_models_ok, "model_call_count": len(model_calls),
                "decisions": metadata["decisions"], "basic_evals_passed": basic["all_passed"]}), flush=True)
    return 0 if all_models_ok and basic["all_passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # 不打印 traceback、locals 或异常链，防止客户端凭据进入终端日志。
        print(f"Trial stopped: {type(exc).__name__}", flush=True)
        raise SystemExit(1)
