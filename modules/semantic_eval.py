"""Independent source-grounded semantic judging; no numeric quality metrics."""
from __future__ import annotations
from modules.skill_library import skill_prompt, skill_snapshot, context_contract

from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from modules.json_output import parse_object
from modules.skill_contracts import DIMENSIONS, SKILLS, digest, plain, serialized, validate_record
from modules.patent_context import CONTRACTS, CONDITIONAL_DIMENSIONS, required_dimensions, handoff_issues

RUBRIC_VERSION = "multidimensional-semantic-v3-document-skills"
VERDICTS = {"acceptable": "可接受", "needs_revision": "需修订", "pending_adjudication": "待裁决"}
EXECUTION_LABELS={"completed":"已完成","partial":"部分完成","not_executed":"未执行","failed":"执行失败","incomplete":"执行不完整"}
FINDING_LABELS={"satisfied":"满足","violated":"违反","undetermined":"未确定","not_applicable":"不适用"}
FINDING_STATUSES = {"satisfied", "violated", "undetermined", "not_applicable"}
JUDGE_SYSTEM = """逐维度返回satisfied/violated/undetermined/not_applicable及非空basis；dimension使用dimensions对象的键，不使用中文标签；必须覆盖全部维度，不得重复。
required_dimensions不能not_applicable；其他不适用也须解释原因。satisfied或violated必须同时引用skill_output
与原始依据（skill_input或原文来源）的逐字引文，每个citation为source_id、quote。引文必须存在，不得改写。
violated须给出具体counterexample和next_action。undetermined须说明缺口和next_action。
只输出JSON：findings数组（dimension,status,basis,citations,counterexample,next_action）、activated_dimensions数组、verdict、summary。
verdict仅acceptable/needs_revision/pending_adjudication；有violated必须needs_revision，无错误但有undetermined
必须pending_adjudication，其余acceptable。禁止分数、概率、权重、通过率、高中低评级及数值质量指标。
参考允许多种有效解释；参考来源不充分不作为权威。裁判意见属于模型辅助复核，不冒称人工或专业认证。"""


def _text(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"语义评估缺少非空文本：{key}")
    return value


def evaluation_packet(record, reference=None):
    record = validate_record(record)
    binding = record["inputs"].get("skill_document")
    if binding and binding != skill_snapshot(record["skill_id"]):
        raise ValueError("Skill 文档与业务执行时不同，请重新执行后评估。")
    reference = plain(reference) if reference is not None else None
    if reference is not None:
        if isinstance(reference,dict) and set(reference)-{"record_hash","provenance","author","basis","alternatives","reviewed_at"}:
            raise ValueError("独立参考包含未约定字段或数值质量指标。")
        if (not isinstance(reference, dict) or reference.get("record_hash") != record["record_hash"]
                or reference.get("provenance") not in {"independent_human_reference", "teaching_reference"}
                or any(not isinstance(reference.get(k), str) or not reference[k].strip() for k in ("author", "basis"))):
            raise ValueError("参考须有独立来源说明、作者、依据和匹配的输出绑定。")
    sources = record["sources"] + [
        {"id": "skill_input", "text": serialized(record["inputs"]), "location": "冻结的技能输入"},
        {"id": "skill_output", "text": serialized(record["output"]), "location": "冻结的实际输出"}]
    contract = context_contract(record["skill_id"])
    from modules.application_baselines import select_baselines
    ctx=record["inputs"].get("patent_context", {})
    gaps=[]
    if not ctx:
        gaps.append("历史记录没有上下文合同，不推定其基准日、范围或版本。")
    elif not ctx.get("cutoff"):
        gaps.append("基准日未提供；能否验收须结合该步骤职责和实际输出。")
    return {"rubric_version": RUBRIC_VERSION, "record": record, "dimensions": DIMENSIONS,
        "judge_document": skill_snapshot("patent-semantic-eval"),
        "required_dimensions": required_dimensions(record), "obligations": contract["check"] + contract["boundary"],
        "skill_document": skill_snapshot(record["skill_id"], include_instructions=True), "context_contract": contract, "context_gaps":gaps,
        "conditional_dimensions":CONDITIONAL_DIMENSIONS,
        "application_baselines":select_baselines(ctx.get("baseline_topics", ["general"])),
        "sources": sources, "reference": reference,
        "reference_status": "none" if reference is None else reference["provenance"]}


def _derive(findings):
    if any(f["status"] == "violated" for f in findings):
        return "needs_revision"
    if any(f["status"] == "undetermined" for f in findings):
        return "pending_adjudication"
    return "acceptable"


def validate_judgment(data, packet):
    old_fields={"findings", "verdict", "summary"}
    if not isinstance(data, dict) or set(data) not in (old_fields,old_fields|{"activated_dimensions"}):
        raise ValueError("语义裁判只接受约定字段，不接受score、概率或额外评级。")
    contextual=bool(packet.get("record",{}).get("inputs",{}).get("patent_context")) or packet.get("kind")=="cross_skill_handoff"
    activated=data.get("activated_dimensions",packet["required_dimensions"])
    if contextual and "activated_dimensions" not in data:
        raise ValueError("上下文语义裁判须明确返回动态必查维度。")
    if (not isinstance(activated,list) or any(not isinstance(d,str) or d not in DIMENSIONS for d in activated)
            or len(set(activated)) != len(activated) or not set(packet["required_dimensions"]) <= set(activated)):
        raise ValueError("动态必查维度遗漏必需项或包含无效项。")
    _text(data, "summary")
    findings = data.get("findings")
    if not isinstance(findings, list):
        raise ValueError("语义裁判缺少维度核验。")
    sources = {s["id"]: s["text"] for s in packet["sources"]}
    seen = set()
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {"dimension", "status", "basis", "citations", "counterexample", "next_action"}:
            raise ValueError("维度记录字段不符合约定。")
        dimension, status = finding["dimension"], finding["status"]
        if not isinstance(dimension,str) or not isinstance(status,str) or dimension not in DIMENSIONS or dimension in seen or status not in FINDING_STATUSES:
            raise ValueError("维度或状态无效、重复。")
        seen.add(dimension)
        _text(finding, "basis")
        for key in ("counterexample", "next_action"):
            if not isinstance(finding[key], str):
                raise ValueError("反例与后续处理须为文本。")
        if status == "not_applicable" and dimension in activated:
            raise ValueError("必要语义维度不能跳过。")
        citations = finding["citations"]
        if not isinstance(citations, list):
            raise ValueError("维度引文须为列表。")
        cited = set()
        for citation in citations:
            if not isinstance(citation, dict) or set(citation) != {"source_id", "quote"}:
                raise ValueError("评估引文格式无效。")
            source_id = citation["source_id"]
            quote = _text(citation, "quote")
            if not isinstance(source_id, str) or source_id not in sources or quote not in sources[source_id]:
                raise ValueError("评估引文无法回查。")
            cited.add(source_id)
        if status in {"satisfied", "violated"} and ("skill_output" not in cited or not cited - {"skill_output"}):
            raise ValueError("语义判断须同时回查实际输出与原始依据。")
        if status == "violated":
            _text(finding, "counterexample")
            _text(finding, "next_action")
        if status == "undetermined":
            _text(finding, "next_action")
    if seen != set(DIMENSIONS):
        raise ValueError("语义裁判遗漏维度。")
    if data["verdict"] != _derive(findings):
        raise ValueError("总裁决与逐维度记录矛盾。")
    return plain(data)


def _identity(client):
    model, url = getattr(client, "model", ""), urlsplit(getattr(client, "url", ""))
    if not isinstance(model, str) or not model.strip() or not url.hostname:
        raise ValueError("独立裁判须有模型与服务来源。")
    # No credentials, query parameters or API keys are persisted.
    host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
    if url.port:
        host += f":{url.port}"
    return {"model": model.strip(), "endpoint": urlunsplit((url.scheme, host, url.path, "", ""))}


def evaluate_skill(record, client, confirmed=False, reference=None):
    if not confirmed:
        raise PermissionError("请先确认向独立裁判发送技能输入、输出、原文及参考。")
    packet = evaluation_packet(record, reference)
    result = {"skill_id": record["skill_id"], "skill_name": SKILLS[record["skill_id"]]["name"],
        "record_hash": record["record_hash"], "rubric_version": RUBRIC_VERSION,
        "execution": "not_executed", "verdict": None, "findings": [],
        "reference_status": packet["reference_status"], "provenance": "model_semantic_judge_not_human",
        "summary": "技能尚未完成，未生成语义验收结论。"}
    if record["execution"] != "completed":
        return result
    identity = _identity(client)
    payload = serialized(packet)
    if len(payload) > 50000:
        raise ValueError("单个技能评估输入超过上限，未调用裁判且未截断。")
    judgment = validate_judgment(parse_object(client.complete(skill_prompt("patent-semantic-eval", JUDGE_SYSTEM), payload, max_tokens=10000)), packet)
    result.update(judgment, execution="completed", judge=identity,
        input_hash=digest(packet), evaluated_at=datetime.now(timezone.utc).isoformat())
    return result


def evaluate_records(records, client, confirmed=False, references=None, *, required_skills=None):
    if not confirmed:
        raise PermissionError("请先确认独立语义评估的外发范围。")
    records = [validate_record(record) for record in records]
    if len({r["skill_id"] for r in records}) != len(records):
        raise ValueError("业务技能记录重复。")
    scope=list(SKILLS) if required_skills is None else list(required_skills)
    if not scope or any(not isinstance(key,str) or key not in SKILLS for key in scope) or len(scope)!=len(set(scope)):
        raise ValueError("语义评估范围须为不重复的已知业务技能。")
    if {r["skill_id"] for r in records}-set(scope):
        raise ValueError("待评输出超出声明的技能范围。")
    refs = references or {}
    if not isinstance(refs, dict) or set(refs) - {r["skill_id"] for r in records}:
        raise ValueError("独立参考包含未知技能。")
    # Validate every packet and size before the first request; never send a partial preflight batch.
    for record in records:
        packet = evaluation_packet(record, refs.get(record["skill_id"]))
        if record["execution"] == "completed" and len(serialized(packet)) > 50000:
            raise ValueError("单个技能评估输入超过上限，整批未发送。")
    from modules.semantic_handoff import handoff_packet, evaluate_handoff
    cross_packet=handoff_packet(records) if set(scope)==set(SKILLS) else None
    if cross_packet is not None and len(serialized(cross_packet)) > 50000:
        raise ValueError("跨步骤衔接评估超过上限，整批未发送且未截断。")
    by_id = {r["skill_id"]: r for r in records}
    results = []
    for skill_id in scope:
        spec=SKILLS[skill_id]
        if skill_id not in by_id:
            results.append({"skill_id": skill_id, "skill_name": spec["name"], "execution": "not_executed",
                "verdict": None, "findings": [], "summary": "没有该技能的实际输出记录。"})
            continue
        try:
            results.append(evaluate_skill(by_id[skill_id], client, True, refs.get(skill_id)))
        except (RuntimeError, ValueError) as exc:
            results.append({"skill_id": skill_id, "skill_name": spec["name"], "record_hash": by_id[skill_id]["record_hash"],
                "execution": "failed", "verdict": None, "findings": [],
                "summary": "独立评估调用或契约校验失败，未接纳裁判结果。"})
    complete = all(r["execution"] == "completed" for r in results)
    verdicts = {r["verdict"] for r in results}
    handoff=None
    if cross_packet is not None and complete:
        try:
            handoff=evaluate_handoff(cross_packet,client)
        except (RuntimeError,ValueError):
            handoff={"execution":"failed","verdict":None,"findings":[],"summary":"跨步骤语义核验失败，未形成整体验收结论。"}
        complete=handoff["execution"]=="completed"
        verdicts.add(handoff["verdict"])
    verdict = None if not complete else ("needs_revision" if "needs_revision" in verdicts else
        "pending_adjudication" if "pending_adjudication" in verdicts else "acceptable")
    return {"schema": "semantic-evaluation-v1", "rubric_version": RUBRIC_VERSION, "scope":scope,
        "records_hash": digest(records), "execution": "completed" if complete else "incomplete", "verdict": verdict,
        "results": results, "handoff":handoff,"handoff_checks":handoff_issues(records),
        "assessment_scope":"skills_and_handoffs" if cross_packet is not None else "individual_skills_only",
        "limits": ["逐维度语义核验不汇总分数。", "模型辅助裁判不是人工确认或专业准确率认证。",
        "未完成的技能不生成验收结论；没有独立参考时如实记录来源边界。"] +
        (["此批记录仅评各技能；上下文缺失或评估范围不足，未形成跨skill语义验收。"] if cross_packet is None else [])}


def evaluation_markdown(report):
    lines = ["# 独立语义评估", "", "执行状态："+EXECUTION_LABELS[report['execution']],
        "整体裁决：" + VERDICTS.get(report.get("verdict"), "未形成完整验收结论")]
    results=list(report["results"])
    if report.get("handoff"):
        results.append(dict(report["handoff"],skill_name="跨skill衔接核验"))
    for result in results:
        lines += ["", "## " + result["skill_name"], "执行状态：" + EXECUTION_LABELS[result["execution"]],
            "裁决：" + VERDICTS.get(result.get("verdict"), "未生成"), result["summary"]]
        for finding in result["findings"]:
            lines += ["", "### " + DIMENSIONS[finding["dimension"]], FINDING_LABELS[finding["status"]] + "：" + finding["basis"]]
            lines += [f"> [{c['source_id']}] {c['quote']}" for c in finding["citations"]]
            if finding["counterexample"]:
                lines.append("反例或缺口：" + finding["counterexample"])
            if finding["next_action"]:
                lines.append("后续处理：" + finding["next_action"])
    return "\n\n".join(lines + report["limits"]) + "\n"
