"""逐篇调用大模型复核；候选、模型复核和历史人工复核保留各自来源。"""
from __future__ import annotations
from modules.skill_library import skill_prompt, skill_snapshot

import json
from dataclasses import asdict
from dataclasses import replace
from urllib.parse import urlsplit, urlunsplit

from modules.json_output import parse_object
from modules.evidence_alignment import locate_evidence, reading_hints
from modules.novelty_models import (DocumentReview, EvidenceRow, Feature, PatentDocument,
                                    check_cutoff, stamp_review)


MODEL_REVIEW_PROMPT_VERSION = "novelty-review-v5-document-skills"
EVIDENCE_ALIGNMENT_INSTRUCTIONS = """disclosed须直接明确支持该要素的全部限定，quote逐字来自指定section_id。
not_found仅表示检查全部输入段落后本篇未找到；资料或含义不明确用unknown。
basis解释限定、等义表达或缺口；同一方案确有原文支持才用同一scheme_id。
"""
MODEL_REVIEW_SYSTEM = EVIDENCE_ALIGNMENT_INSTRUCTIONS + """仅审查当前这一篇文献。
context_complete表示输入上下文是否足以完成此次比对；摘要、局部摘录或引用外部未提供内容不能确认完整。
parsed_complete仅为解析结构提示，不证明语义完整。充分公开或同一方案无法判断时对应布尔值为false。
仅输出JSON对象，字段如下（所有字段必填，布尔值必须是JSON布尔值）：
document_id，rows（每项feature_id、status(disclosed/not_found/unknown)、section_id、quote、scheme_id、basis），
same_scheme_confirmed，enabling_disclosure_confirmed，same_scheme_basis，scheme_section_ids（原文段落ID数组），
relevance(related/unrelated/unknown)，relevance_basis，relevance_section_ids（段落ID数组），
context_complete，completeness_basis，reviewed_section_ids（实际检查的全部输入段落ID，不得遗漏）。
不输出reviewed、reviewer、origin、时间或哈希，这些由程序按模型来源记录。"""


def model_review_payload(features: list[Feature], document: PatentDocument, cutoff: str,
                         candidate: DocumentReview | None = None,
                         evidence_hits: list[dict] | None = None, *, patent_context=None) -> dict:
    """外发内容与界面预览共用，保留完整快照，不静默截断技术限定。"""
    if evidence_hits is None:
        # 此处只生成候选；真实来源门槛仍由Harness及最终规则负责。
        evidence_hits, located = locate_evidence(features, [document], allow_synthetic=True)
        candidate = candidate or located[0]
    from modules.patent_context import context_for
    from modules.application_baselines import select_baselines
    ctx = context_for(features, cutoff, patent_context)
    return {"features": [asdict(f) for f in features], "cutoff": cutoff,
            "patent_context": ctx, "application_baselines": select_baselines(ctx["baseline_topics"]),
            "document": document.to_dict() | {"snapshot_hash": document.snapshot_hash},
            "candidate_rows": [asdict(row) for row in candidate.rows] if candidate else [],
            "retrieval_hints": reading_hints(features, document, evidence_hits),
            "evidence_alignment": {"primary": "model_semantics", "retrieval": "reading_hints_only",
                                   "coverage": "all_snapshot_sections"},
            "prompt_version": MODEL_REVIEW_PROMPT_VERSION}


def _text(data: dict, key: str, *, required: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"模型复核字段 {key} 须为{'非空' if required else ''}文本。")
    return value


def _locations(data: dict, key: str, sections: dict) -> tuple[str, ...]:
    value = data.get(key)
    if (not isinstance(value, list) or any(not isinstance(x, str) for x in value)
            or len(value) != len(set(value)) or not set(value) <= set(sections)):
        raise ValueError(f"模型复核的 {key} 存在无效或重复原文位置。")
    return tuple(value)


def validate_evidence_rows(raw_rows: object, features: list[Feature], document: PatentDocument) -> tuple[EvidenceRow, ...]:
    """模型建议和逐项复核共用的要素覆盖、解释及逐字出处校验。"""
    if not isinstance(raw_rows, list):
        raise ValueError("模型复核缺少 rows 数组。")
    sections = {section.id: section.text for section in document.sections}
    rows = {}
    feature_ids = {feature.id for feature in features}
    for value in raw_rows:
        if not isinstance(value, dict):
            raise ValueError("模型复核要素行须为对象。")
        fields = {key: _text(value, key, required=key in {"feature_id", "status", "basis"})
                  for key in ("feature_id", "status", "section_id", "quote", "scheme_id", "basis")}
        if (fields["feature_id"] not in feature_ids or fields["feature_id"] in rows
                or fields["status"] not in {"disclosed", "not_found", "unknown"}):
            raise ValueError("模型复核要素存在未知、重复编号或无效状态。")
        if fields["section_id"] and fields["section_id"] not in sections:
            raise ValueError("模型复核要素引用了不存在的原文位置。")
        if fields["quote"] and (not fields["quote"].strip()
                                or fields["quote"] not in sections.get(fields["section_id"], "")):
            raise ValueError("模型复核引文无法逐字回查原文，未接纳本次复核。")
        if fields["status"] == "disclosed" and (not fields["quote"].strip() or not fields["scheme_id"].strip()):
            raise ValueError("模型披露结论缺少引文或方案编号。")
        rows[fields["feature_id"]] = EvidenceRow(**fields, origin="model")
    if set(rows) != feature_ids:
        raise ValueError("模型复核遗漏技术要素。")
    return tuple(rows[feature.id] for feature in features)


def _validated_model_review(data: dict, features: list[Feature], document: PatentDocument,
                             cutoff: str, model: str, endpoint: str, *, patent_context=None) -> DocumentReview:
    """机器只验证契约和逐字出处；语义判定仍是模型输出，不生成虚假的人工身份。"""
    if data.get("document_id") != document.id:
        raise ValueError("模型复核返回的文献编号不匹配。")
    sections = {section.id: section.text for section in document.sections}
    locations = {key: _locations(data, key, sections) for key in
                 ("scheme_section_ids", "relevance_section_ids", "reviewed_section_ids")}
    if set(locations["reviewed_section_ids"]) != set(sections):
        raise ValueError("模型复核未检查全部输入段落，未接纳本次复核。")
    for key in ("same_scheme_confirmed", "enabling_disclosure_confirmed", "context_complete"):
        if type(data.get(key)) is not bool:
            raise ValueError(f"模型复核字段 {key} 必须是布尔值。")
    relevance = _text(data, "relevance")
    if relevance not in {"related", "unrelated", "unknown"}:
        raise ValueError("模型复核的技术相关性无效。")
    relevance_basis = _text(data, "relevance_basis", required=True)
    if relevance != "unknown" and not locations["relevance_section_ids"]:
        raise ValueError("模型相关性结论缺少原文位置。")
    scheme_basis = _text(data, "same_scheme_basis")
    if data["same_scheme_confirmed"] and (not scheme_basis.strip() or not locations["scheme_section_ids"]):
        raise ValueError("模型同一方案确认缺少原文依据。")
    rows = validate_evidence_rows(data.get("rows"), features, document)
    review = DocumentReview(document.id, rows,
        reviewed=True, same_scheme_confirmed=data["same_scheme_confirmed"],
        enabling_disclosure_confirmed=data["enabling_disclosure_confirmed"],
        reviewer=f"模型：{model}", same_scheme_basis=scheme_basis,
        scheme_section_ids=locations["scheme_section_ids"], relevance=relevance,
        relevance_basis=relevance_basis, relevance_section_ids=locations["relevance_section_ids"],
        review_method="model", review_model=model, review_endpoint=endpoint,
        review_prompt_version=MODEL_REVIEW_PROMPT_VERSION, context_complete=data["context_complete"],
        completeness_basis=_text(data, "completeness_basis", required=True),
        reviewed_section_ids=locations["reviewed_section_ids"])
    from modules.patent_context import context_for
    return stamp_review(review, features, document, cutoff,
                        patent_context=context_for(features,cutoff,patent_context))


def model_review_documents(features: list[Feature], documents: list[PatentDocument], cutoff: str,
                            client, confirmed: bool,
                            candidates: list[DocumentReview] | None = None, *,
                            evidence_hits: list[dict] | None = None, patent_context=None) -> list[DocumentReview]:
    """每篇单独请求；全批次验证成功才返回，失败不接纳半成品或回退为人工确认。"""
    if not confirmed:
        raise PermissionError("请先确认向模型发送的要素、原文及候选对照。")
    check_cutoff(cutoff)
    if (not features or len({f.id for f in features}) != len(features)
            or not documents or len({d.id for d in documents}) != len(documents)):
        raise ValueError("模型复核需要非空且编号唯一的要素和文献。")
    if any(not d.sections or d.error or len({s.id for s in d.sections}) != len(d.sections) for d in documents):
        raise ValueError("待复核文献正文缺失、位置重复或获取失败。")
    model = getattr(client, "model", "").strip()
    parsed = urlsplit(getattr(client, "url", ""))
    if not model or not parsed.hostname:
        raise ValueError("模型复核需要有效的模型名和服务地址。")
    # 不记录 URL 中的账号、密码、查询参数或 API Key。
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = host + (f":{parsed.port}" if parsed.port else "")
    endpoint = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    by_id = {review.document_id: review for review in candidates or []}
    payloads = [json.dumps(model_review_payload(features, doc, cutoff, by_id.get(doc.id), evidence_hits,
                                               patent_context=patent_context), ensure_ascii=False)
                for doc in documents]
    if any(len(payload) > 50000 for payload in payloads):
        raise ValueError("单篇模型复核输入超过 5 万字符，请缩小完整比对范围；原文未截断，未发出请求。")
    result = []
    for doc, payload in zip(documents, payloads):
        data = parse_object(client.complete(skill_prompt("patent-evidence-alignment", MODEL_REVIEW_SYSTEM), payload, max_tokens=10000))
        result.append(_validated_model_review(data, features, doc, cutoff, model, endpoint,patent_context=patent_context))
    return result


def empty_review_rows(features: list[Feature]) -> tuple[EvidenceRow, ...]:
    return tuple(EvidenceRow(f.id, basis="", origin="human") for f in features)


def adopt_candidate_quotes(rows: tuple[EvidenceRow, ...], candidates: tuple[EvidenceRow, ...],
                           selected_ids: list[str]) -> tuple[EvidenceRow, ...]:
    candidates_by_id = {row.feature_id: row for row in candidates}
    result = []
    for row in rows:
        candidate = candidates_by_id.get(row.feature_id)
        if row.feature_id in selected_ids and candidate and candidate.quote.strip():
            row = replace(row, section_id=candidate.section_id, quote=candidate.quote,
                          status="unknown", scheme_id="", basis="", origin=candidate.origin)
        result.append(row)
    return tuple(result)


def edited_review_rows(edited: list[dict], baseline: tuple[EvidenceRow, ...]) -> tuple[EvidenceRow, ...]:
    previous = {row.feature_id: row for row in baseline}
    result = []
    for value in edited:
        row = EvidenceRow(**value)
        old = previous.get(row.feature_id)
        if old:
            if row.quote != old.quote or row.section_id != old.section_id:
                row = replace(row, origin="human")
            changed = any(getattr(row, key) != getattr(old, key)
                          for key in ("status", "quote", "section_id", "scheme_id"))
            if changed and row.basis == old.basis:
                row = replace(row, basis="")  # The old explanation cannot silently describe a new status.
        result.append(row)
    return tuple(result)
