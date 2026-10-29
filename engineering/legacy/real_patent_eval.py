"""Real-source observations, independent references and synthetic regression are separate contracts."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import requests

from modules.google_patent_documents import parse_google_page
from modules.novelty_models import Feature, PatentDocument
from modules.novelty_report import to_markdown
from skills.report_delivery_skill import build_report as report_payload
from modules.novelty_search import search_input_hash


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


def fetch_sources(destination: Path, publications: list[str], get=None) -> None:
    """Explicit opt-in: at most two known public pages, no overwrite or model call."""
    if (len(publications) != 2 or len(set(publications)) != 2
            or any(not re.fullmatch(r"[A-Za-z0-9]+", p) for p in publications)):
        raise ValueError("本真实来源集固定使用两篇文献。")
    destination.mkdir(parents=True, exist_ok=False)
    get = get or requests.get
    records = []
    for publication in publications:
        record = {"publication_id": publication, "started_at": datetime.now(timezone.utc).isoformat()}
        try:
            url = f"https://patents.google.com/patent/{publication}/en"
            response = get(url, headers={"User-Agent": "PatentNoveltyHarness/1.0"},
                           timeout=(5, 30), allow_redirects=False)
            record["http_status"] = response.status_code
            raw = destination / f"{publication}.html"
            raw.write_bytes(response.content)
            record.update(html_file=raw.name, html_sha256=digest(raw))
            if 300 <= response.status_code < 400:
                raise ValueError("不沿重定向自动建立来源。")
            response.raise_for_status()
            doc = parse_google_page(url, response.text)
            if doc.id != publication or not doc.sections or doc.error:
                raise ValueError("来源正文缺失或编号不符。")
            snapshot = destination / f"{publication}_source.json"
            write_json(snapshot, doc.to_dict() | {"snapshot_hash": doc.snapshot_hash})
            record.update(snapshot_file=snapshot.name, file_sha256=digest(snapshot), status="ok")
        except (requests.RequestException, ValueError) as exc:
            record.update(status="error", error=str(exc))
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        records.append(record)
    write_json(destination / "source_manifest.json", {"schema_version": 1, "documents": records})
    if any(record["status"] != "ok" for record in records):
        raise ValueError("真实来源获取不完整，失败记录已保留；不能运行完整比较。")


def load_sources(folder: Path, publications: list[str]) -> list[PatentDocument]:
    manifest = json.loads((folder / "source_manifest.json").read_text(encoding="utf-8"))
    entries = manifest["documents"]
    if len(entries) != len(publications) or {e["publication_id"] for e in entries} != set(publications):
        raise ValueError("来源清单与测试集不符。")
    documents = []
    for entry in entries:
        if entry.get("status") != "ok":
            raise ValueError("来源清单含失败项。")
        files = {}
        for field, hash_field in (("snapshot_file", "file_sha256"), ("html_file", "html_sha256")):
            path = (folder / entry[field]).resolve()
            if not path.is_relative_to(folder.resolve()) or digest(path) != entry[hash_field]:
                raise ValueError("来源文件路径或哈希不符。")
            files[field] = path
        raw = json.loads(files["snapshot_file"].read_text(encoding="utf-8"))
        if not raw.get("snapshot_hash"):
            raise ValueError("来源快照必须携带哈希。")
        doc = PatentDocument.from_dict(raw)
        if (doc.id != entry["publication_id"] or doc.source_kind != "google_patents_page"
                or doc.snapshot_schema_version != 2 or not doc.sections):
            raise ValueError("真实来源集须使用带原文定位的网络快照。")
        # File hashes alone do not prove that the snapshot corresponds to the raw page.
        parsed = parse_google_page(doc.url, files["html_file"].read_text(encoding="utf-8"))
        if (parsed.id != doc.id or parsed.sections != doc.sections or doc.complete
                or any(getattr(parsed, field) != getattr(doc, field)
                       for field in ("publication_date", "filing_date", "priority_date", "title"))):
            raise ValueError("快照与保存的原始HTML不一致。")
        documents.append(doc)
    return documents


def evaluation_input_hash(features: list[Feature], documents: list[PatentDocument], cutoff: str) -> str:
    raw = json.dumps({"feature_cutoff": search_input_hash(features, cutoff),
                      "documents": sorted((d.id, d.snapshot_hash) for d in documents)}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def real_cases(root: Path) -> tuple[dict, list[dict], list[str]]:
    definition = json.loads((root / "evals/legacy/real_patent_cases.json").read_text(encoding="utf-8"))
    example = json.loads((root / definition["example"]).read_text(encoding="utf-8"))
    cases = []
    for item in definition["cases"]:
        features = [Feature(f["id"], f["text"]) for f in example["features"]]
        if item["mutation_id"]:
            mutation = next(m for m in example["mutations"] if m["id"] == item["mutation_id"])
            features = [replace(f, text=mutation["text"]) if f.id == mutation["feature_id"] else f for f in features]
        cases.append({"id": item["id"], "features": features, "cutoff": example["cutoff"]})
    return definition, cases, [example["source_patent"], example["reference_patent"]]


def summarize_real(observations: list[dict], references: list[dict]) -> dict:
    """References are read after predictions. AI drafts and stale references never count as gold."""
    if len({o["case_id"] for o in observations}) != len(observations):
        raise ValueError("真实案例编号重复。")
    if not isinstance(references, list) or any(not isinstance(r, dict) for r in references):
        raise ValueError("参考答案须为独立记录列表。")
    accepted, rejected = {}, []
    by_id = {o["case_id"]: o for o in observations}
    seen = set()
    for reference in references:
        case_id = reference.get("case_id", "")
        if case_id in seen:
            raise ValueError("参考答案编号重复，不能任选其一。")
        seen.add(case_id)
        actual = by_id.get(case_id)
        try:
            time_valid = datetime.fromisoformat(reference.get("reviewed_at", "")).utcoffset() is not None
        except (TypeError, ValueError):
            time_valid = False
        if (actual is None or reference.get("provenance") != "human_reviewed"
                or reference.get("author_type") != "human"
                or reference.get("input_hash") != actual["input_hash"] or not time_valid
                or any(not isinstance(reference.get(field), str) or not reference[field].strip()
                       for field in ("reviewer", "basis"))
                or reference.get("label") not in {"potential_novelty_risk", "no_complete_disclosure_found", "insufficient_evidence"}):
            rejected.append({"case_id": case_id, "reason": "非独立人工复核记录、缺少身份/时刻/说明，或输入绑定不符"})
            continue
        accepted[case_id] = reference
    scored = [(o, accepted[o["case_id"]]) for o in observations if o["case_id"] in accepted]
    risk = [(o, r) for o, r in scored if r["label"] == "potential_novelty_risk"]
    cleared = [(o, r) for o, r in scored if o["label"] == "no_complete_disclosure_found"]
    return {"set_kind": "real_patent", "observed_case_count": len(observations), "reference_case_count": len(scored),
            "unscored_case_count": len(observations) - len(scored), "risk_reference_count": len(risk),
            "scored_clearance_count": len(cleared),
            "risk_miss_count": sum(o["label"] != "potential_novelty_risk" for o, _ in risk) if risk else None,
            "false_clearance_count": sum(r["label"] != "no_complete_disclosure_found" for _, r in cleared) if scored else None,
            "false_alarm_count": sum(o["label"] == "potential_novelty_risk" and r["label"] == "no_complete_disclosure_found" for o, r in scored) if scored else None,
            "abstention_count": sum(o["label"] == "insufficient_evidence" for o in observations),
            "rejected_references": rejected,
            "metric_definitions": {"risk_miss_count": "风险参考题中未报risk的数量，包含拒答；没有风险参考题时为null",
                "false_clearance_count": "有参考题中实际放行而参考非放行的数量；没有合格参考时为null",
                "false_alarm_count": "参考未发现完整披露、实际报risk；没有合格参考时为null",
                "abstention_count": "全部真实运行中的insufficient_evidence数量；与漏报可能重叠"},
            "limits": ["没有独立参考答案时不能计算专业正确率，null不等于0。", "人工身份为自填，程序不认证其独立性或专业资质。",
                       "同一专利族的原方案和变更版不是独立盲测样本；固定快照比对不评在线检索召回。"]}


def run_real_evaluation(root: Path, source_dir: Path, output: Path, references: Path | None = None,
                        harness=None) -> dict:
    from skills.precheck_skill import NoveltyHarness
    definition, cases, publications = real_cases(root)
    documents = load_sources(source_dir, publications)
    output.mkdir(parents=True, exist_ok=False)
    harness = harness or NoveltyHarness()
    observations = []
    for case in cases:
        # Explicit allowlist: no reference labels, draft reviews or answer-key fields enter the harness.
        state = harness.run(features=case["features"], cutoff=case["cutoff"], documents=documents)
        payload = report_payload(state, case["id"], case["cutoff"], harness.lexicon.groups)
        write_json(output / f"{case['id']}_evidence.json", payload)
        (output / f"{case['id']}_report.md").write_text(to_markdown(payload), encoding="utf-8")
        observations.append({"case_id": case["id"], "input_hash": evaluation_input_hash(case["features"], documents, case["cutoff"]),
                             "label": state["decision"].label, "source_hashes": {d.id: d.snapshot_hash for d in documents},
                             "features": [asdict(f) for f in case["features"]], "cutoff": case["cutoff"]})
    # Persist original predictions before opening references; neither labels nor reference rows affect execution.
    write_json(output / "observations.json", observations)
    reference_data = json.loads(references.read_text(encoding="utf-8")) if references else []
    summary = summarize_real(observations, reference_data)
    report = {"dataset_version": definition["dataset_version"], "provenance": definition["provenance"],
              "created_at": datetime.now(timezone.utc).isoformat(), "summary": summary,
              "observations": observations, "references": reference_data,
              "source_manifest_sha256": digest(source_dir / "source_manifest.json"),
              "source_manifest": json.loads((source_dir / "source_manifest.json").read_text(encoding="utf-8"))}
    write_json(output / "real_eval.json", report)
    template = [{"case_id": o["case_id"], "input_hash": o["input_hash"], "provenance": "unreviewed",
                 "author_type": "", "reviewer": "", "reviewed_at": "", "label": None, "basis": ""} for o in observations]
    write_json(output / "reference_template.json", template)
    display = lambda value: "不可计算（缺少相应独立参考）" if value is None else str(value)
    lines = ["# 真实来源运行与参考答案状态", "", "固定公开快照比较；本轮不执行在线召回、不调用付费模型、不创建人工复核。", "",
             f"真实运行：{summary['observed_case_count']}个方案；合格参考：{summary['reference_case_count']}；"
             f"保留证据不足：{summary['abstention_count']}。同源原版和变更版不是独立盲测样本。", "",
             f"风险漏报：{display(summary['risk_miss_count'])}；错误放行：{display(summary['false_clearance_count'])}；"
             f"风险误报：{display(summary['false_alarm_count'])}。", ""]
    lines += [f"- {o['case_id']}：{o['label']}；输入绑定：`{o['input_hash']}`" for o in observations]
    lines += ["", "## 来源与取得方式", f"清单SHA-256：`{report['source_manifest_sha256']}`"]
    lines += [f"- {entry['publication_id']}：{entry.get('provenance', '本次显式获取')}；"
              f"HTML SHA-256=`{entry['html_sha256']}`；快照文件SHA-256=`{entry['file_sha256']}`"
              for entry in report["source_manifest"]["documents"]]
    lines += ["", "## 指标定义"] + [f"- {key}：{value}" for key, value in summary["metric_definitions"].items()]
    lines += ["", "## 限制"] + [f"- {value}" for value in summary["limits"]]
    (output / "real_eval.md").write_text("\n\n".join(lines), encoding="utf-8")
    return report
