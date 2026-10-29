"""Business skill contracts. Operational frameworks are not business skills."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import math

CONTRACT_VERSION = "business-skills-v1"
DIMENSIONS = {
    "terms_objects": "术语与对象", "structure_relations": "结构与关系",
    "logic_constraints": "逻辑与限定", "context_reference": "语境与指称",
    "functions_effects": "功能与效果", "evaluation_expression": "评价与表达",
}
SKILLS = {
    "patent-material-preparation": {"name": "材料整理", "required": ("terms_objects", "structure_relations", "logic_constraints", "context_reference"),
        "obligations": "逐项保留材料支持的对象、关系和限定；不补造结构或效果；未知不改为不存在；用户确认输入不冒称模型读过附件。"},
    "patent-synonym-expansion": {"name": "术语扩展", "required": ("terms_objects", "logic_constraints", "context_reference"),
        "obligations": "结合技术语境区分等义、上下位和相关概念；保护原子术语、否定、方向、单位和完整关系；检索有用不证明语义等价；新增词不自动成为法律等同。"},
    "patent-candidate-search": {"name": "候选检索", "required": ("terms_objects", "structure_relations", "context_reference"),
        "obligations": "说明实际查询与读取范围；区分同名提及与技术关系、元数据线索与正文披露；背景中的真实技术描述仍可相关；失败和未读保留未知。"},
    "patent-evidence-alignment": {"name": "证据对照", "required": ("terms_objects", "structure_relations", "logic_constraints", "context_reference"),
        "obligations": "逐项用原文支持完整限定；核查方向、条件、指代、可选与互斥方案；不只引用相似名词；引文真实不等于语义支持；阅读线索不是最终披露判断。"},
    "patent-novelty-assessment": {"name": "新颖性判断", "required": ("structure_relations", "logic_constraints", "context_reference"),
        "obligations": "根据有效逐项复核核验同一方案整体公开；不跨文献或互斥方案拼接；区分明确支持、必然隐含与仅相容；材料充分时不能泛泛拒答；结论仅限预筛范围。"},
    "patent-report-delivery": {"name": "报告交付", "required": ("logic_constraints", "context_reference", "evaluation_expression"),
        "obligations": "报告忠于输入、证据和决定；保留检索缺口和未知；不把模型意见称人工确认；不把未找到扩大为不存在或可授权；表达修饰不改变技术结论。"},
}
EXECUTIONS = {"completed", "partial", "not_executed", "failed"}
# These fields may exist in retrieval audit logs, but never enter a semantic judge's quality input.
OPERATIONAL_FIELDS = {"score", "threshold", "rank", "ranking_hint", "lexical_hint", "relation_score", "cost",
    "retrieval_cost", "generation_cost", "metrics", "recall_at_k", "precision_at_k", "mrr", "usage"}


def plain(value):
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("记录键须为文本。")
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("技能记录包含不可序列化的值。")


def semantic_view(value, operational=False):
    value = plain(value)
    if isinstance(value, dict):
        return {k: semantic_view(v, operational or k in {"search_audit","reading_hints","retrieval_hints","generation"})
                for k,v in value.items() if not (operational and k in OPERATIONAL_FIELDS)}
    if isinstance(value, list):
        return [semantic_view(v, operational) for v in value]
    return value


def serialized(value):
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(serialized(value).encode("utf-8")).hexdigest()


def make_record(skill_id, inputs, output, *, sources=(), execution="completed", producer="program", note=""):
    if not isinstance(skill_id,str) or not isinstance(execution,str) or skill_id not in SKILLS or execution not in EXECUTIONS:
        raise ValueError("未知技能或执行状态。")
    if not isinstance(producer, str) or not producer.strip() or not isinstance(note, str):
        raise ValueError("须记录执行来源与说明。")
    clean_sources = []
    for source in sources:
        if (not isinstance(source, dict) or not isinstance(source.get("id"), str) or not source["id"].strip()
                or not isinstance(source.get("text"), str) or not source["text"].strip()):
            raise ValueError("原文来源须有唯一编号和文本。")
        clean_sources.append({"id": source["id"], "text": source["text"], "location": str(source.get("location", ""))})
    if len({s["id"] for s in clean_sources}) != len(clean_sources) or any(s["id"].startswith("skill_") for s in clean_sources):
        raise ValueError("原文来源编号重复或使用了保留前缀。")
    record = {"contract_version": CONTRACT_VERSION, "skill_id": skill_id, "skill_name": SKILLS[skill_id]["name"],
        "execution": execution, "producer": producer, "note": note, "inputs": semantic_view(inputs),
        "output": semantic_view(output), "sources": clean_sources}
    record["record_hash"] = digest(record)
    return record


def validate_record(record):
    if not isinstance(record, dict) or record.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("不是当前业务技能记录。")
    if set(record) != {"contract_version","skill_id","skill_name","execution","producer","note","inputs","output","sources","record_hash"}:
        raise ValueError("技能记录字段缺失或存在额外字段。")
    content = {k: v for k, v in record.items() if k != "record_hash"}
    if record.get("record_hash") != digest(content):
        raise ValueError("技能记录绑定失效。")
    rebuilt = make_record(record["skill_id"], record["inputs"], record["output"], sources=record["sources"],
        execution=record["execution"], producer=record["producer"], note=record["note"])
    if rebuilt != record:
        raise ValueError("技能记录字段不符合契约。")
    return plain(record)


def document_sources(documents):
    return [{"id": f"{doc.id}:{section.id}", "text": section.text,
             "location": section.source_location or section.id} for doc in documents for section in doc.sections]


def upsert_record(records, record):
    validate_record(record)
    by_id = {r["skill_id"]: r for r in records}
    by_id[record["skill_id"]] = record
    return [by_id[key] for key in SKILLS if key in by_id]
