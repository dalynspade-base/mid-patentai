"""CNIPA 新颖性预筛规则。结论受限于已检索、已复核的 Google Patents 快照。

禁止跨文献/互斥实施例拼接。权利要求引用在入口按明确分支展开；此处逐个完整目标判断。
当前不处理创造性、FTO或宽限期。
同义词和检索分数不进入法律判断；模型复核须通过出处、覆盖和输入绑定校验。
"""

from __future__ import annotations

from datetime import date, datetime

from modules.novelty_models import (
    DocumentReview, Feature, NoveltyDecision, PatentDocument, check_cutoff, google_patent_url,
    review_input_hash,
)
from modules.novelty_search import search_warnings


def assess_novelty(features: list[Feature], documents: list[PatentDocument],
                   reviews: list[DocumentReview], cutoff: str, *, task: str = "novelty",
                   errors: list[str] | None = None, allow_synthetic: bool = False,
                   material_unknowns: list[str] | None = None,
                   material_warnings: list[str] | None = None,
                   search_audit: dict | None = None, patent_context=None) -> NoveltyDecision:
    if task != "novelty":
        return NoveltyDecision("out_of_scope", ["仅支持新颖性预筛，不输出侵权、FTO 或可授权性判断。"])
    baseline = check_cutoff(cutoff)
    if not features or len({f.id for f in features}) != len(features) or any(not f.text.strip() for f in features):
        raise ValueError("技术要素不能为空且编号必须唯一。")
    if len({d.id for d in documents}) != len(documents):
        raise ValueError("文献编号重复，请先按公开号去重。")
    warnings = list(errors or [])
    warnings.extend(search_warnings(search_audit, features, cutoff))
    for values, prefix in ((material_unknowns, "材料未知项："), (material_warnings, "材料核查缺口：")):
        if values is not None and (not isinstance(values, list) or any(not isinstance(x, str) for x in values)):
            raise ValueError("材料未知项与核查缺口须为文字列表。")
        warnings.extend(prefix + value.strip() for value in values or [] if value.strip())
    warnings = list(dict.fromkeys(warnings))
    review_by_id = {review.document_id: review for review in reviews}
    if len(review_by_id) != len(reviews) or set(review_by_id) - {doc.id for doc in documents}:
        warnings.append("存在重复或不属于当前文献集的复核，不能用于判断。")
        return NoveltyDecision("insufficient_evidence", warnings)
    model_notes = (["逐项复核由大模型完成，逐字回查不证明语义判断正确；模型复核不等于人工或专业确认。"]
                   if any(r.review_method == "model" for r in reviews) else [])
    fully_reviewed = 0
    ordinary_count = 0
    risk_ids: list[str] = []
    for doc in documents:
        try:
            google_patent_url(doc.url)
        except ValueError:
            warnings.append(f"{doc.id}：不是允许的 Google Patents 来源。")
            continue
        if doc.source_kind == "synthetic_eval" and not allow_synthetic:
            warnings.append(f"{doc.id}：教学合成资料不能进入真实预筛。")
            continue
        if doc.source_kind not in {"google_patents_page", "manual_google_patents_snapshot", "synthetic_eval"}:
            warnings.append(f"{doc.id}：来源类型无效，不能用于预筛。")
            continue
        if doc.error or not doc.sections:
            warnings.append(f"{doc.id}：正文缺失或获取失败。")
            continue
        try:
            published = date.fromisoformat(doc.publication_date)
        except (TypeError, ValueError):
            warnings.append(f"{doc.id}：缺少有效公开日期。")
            continue
        if published >= baseline:
            # 不能仅按公开日期丢弃潜在抵触申请；优先权与申请历史也不能自动等同。
            earlier = []
            for raw in (doc.filing_date, doc.priority_date):
                if raw:
                    try:
                        earlier.append(date.fromisoformat(raw))
                    except ValueError:
                        warnings.append(f"{doc.id}：申请日或优先权日无效，需要核验。")
            if doc.id.upper().startswith("CN") and (not earlier or min(earlier) < baseline):
                warnings.append(f"{doc.id}：可能涉及抵触申请或日期链不完整，转人工核验。")
            else:
                warnings.append(f"{doc.id}：未作为基准日前普通现有技术使用；日期及同族仍需核验。")
            continue
        ordinary_count += 1
        review = review_by_id.get(doc.id)
        if not review or not review.reviewed or review.review_method == "candidate":
            warnings.append(f"{doc.id}：对照尚未完成逐项复核。")
            continue
        if patent_context is not None:
            from modules.candidate_relevance import valid_body_review
            if not valid_body_review(review,features,doc,cutoff,patent_context=patent_context):
                warnings.append(f"{doc.id}：复核与当前语境未绑定或已失效，需要重新复核。")
                continue
        try:
            reviewed_at = datetime.fromisoformat(review.reviewed_at)
            time_valid = reviewed_at.utcoffset() is not None
        except (ValueError, TypeError):
            time_valid = False
        if (not review.reviewer.strip() or not time_valid
                or review.input_hash != review_input_hash(features, doc, cutoff, review)):
            warnings.append(f"{doc.id}：复核来源/执行者、时刻或输入绑定缺失/已失效，请重新复核。")
            continue
        section_ids = {s.id for s in doc.sections}
        if review.review_method == "model" and (
                not review.review_model.strip() or not review.review_endpoint.strip()
                or not review.review_prompt_version.strip() or not review.completeness_basis.strip()
                or set(review.reviewed_section_ids) != section_ids
                or len(review.reviewed_section_ids) != len(section_ids)
                or any(row.origin != "model" for row in review.rows)):
            warnings.append(f"{doc.id}：模型复核来源、上下文依据或段落覆盖记录不完整。")
            continue
        if (review.relevance not in {"related", "unrelated"} or not review.relevance_basis.strip()
                or not review.relevance_section_ids or not set(review.relevance_section_ids) <= section_ids):
            warnings.append(f"{doc.id}：技术相关性未确认，或缺少说明及原文位置；词汇分数不能代替相关性。")
            continue
        if review.relevance == "unrelated":
            warnings.append(f"{doc.id}：复核标为不相关，不能据此排除待判断方案的完整披露风险。")
            continue
        by_feature = {row.feature_id: row for row in review.rows}
        if len(by_feature) != len(review.rows) or set(by_feature) != {f.id for f in features}:
            warnings.append(f"{doc.id}：要素对照存在重复、遗漏或额外编号。")
            continue
        sections = {section.id: section.text for section in doc.sections}
        valid = True
        disclosed = []
        for feature in features:
            row = by_feature[feature.id]
            if row.status not in {"disclosed", "not_found", "unknown"} or row.status == "unknown":
                valid = False
            elif not row.basis.strip() or row.origin not in {"auto", "model", "human"}:
                valid = False
            elif row.status == "disclosed":
                # 逐字回查只是出处检查，技术含义由有来源记录的复核方判断。
                if (not row.quote.strip() or not row.scheme_id.strip()
                        or row.quote not in sections.get(row.section_id, "")):
                    valid = False
                disclosed.append(row)
        if not valid:
            warnings.append(f"{doc.id}：存在未知要素、无效状态、缺失复核说明或无法回查的引文。")
            continue
        context_complete = (review.context_complete and (doc.complete or doc.parsed_complete)
                            if review.review_method == "model" else doc.complete)
        if not context_complete:
            warnings.append(f"{doc.id}：资料不完整，不能据局部摘录排除完整披露。")
            continue
        fully_reviewed += 1
        if (len(disclosed) == len(features) and len({r.scheme_id for r in disclosed}) == 1
                and review.same_scheme_confirmed and review.enabling_disclosure_confirmed
                and review.same_scheme_basis.strip() and review.scheme_section_ids
                and set(review.scheme_section_ids) <= set(sections)
                and {r.section_id for r in disclosed} <= set(review.scheme_section_ids)):
            risk_ids.append(doc.id)
        elif len(disclosed) == len(features):
            warnings.append(f"{doc.id}：完整要素分属不同方案，或同一方案/充分公开的确认及原文依据不足。")
            fully_reviewed -= 1
    if risk_ids:
        return NoveltyDecision("potential_novelty_risk", [
            "至少一篇基准日前文献经逐项复核，在同一技术方案中披露全部已确认要素。",
            "这是新颖性预筛风险提示，不能推导出完整授权结论。",
        ], risk_ids, warnings + model_notes)
    if documents and ordinary_count and fully_reviewed == ordinary_count == len(documents) and not warnings:
        return NoveltyDecision("no_complete_disclosure_found", [
            "逐篇对照后，本次已复核文献中未发现一篇完整披露全部要素的文献。",
            "未跨文献拼接；这不证明不存在其他现有技术，也不等于可以授权。",
        ], warnings=model_notes)
    return NoveltyDecision("insufficient_evidence", [
        "存在资料、日期、检索、要素或复核缺口，不能排除新颖性风险。",
    ], warnings=(warnings or ["没有取得可复核的文献。"]) + model_notes)
