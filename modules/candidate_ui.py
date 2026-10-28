"""候选相关性重排与追加正文的界面，保留原词/扩展来源及已有证据。"""
import hashlib
import json
from copy import deepcopy

import streamlit as st

from modules.candidate_ranking import RANKING_VERSION, rank_candidates, metadata_contexts
from modules.candidate_relevance import (MAX_CANDIDATES, MAX_FEATURE_PAIRS, fetch_additional_documents,
    metadata_input_hash, model_judge_candidates)
from modules.candidate_relevance import annotate_origins
from modules.google_patent_documents import fetch_google_document
from modules.llm_client import LLMClient


def rerun_with_evidence(state, harness, cutoff, material_context, *, audit, documents=None, reviews=None):
    updated = harness.run(features=state["features"], cutoff=cutoff,
        documents=state["documents"] if documents is None else documents,
        reviews=state.get("reviews", []) if reviews is None else reviews,
        search_audit=audit, errors=state.get("errors", []),
        material_original=state.get("material_original",""),
        patent_context=state.get("patent_context"),
        **({"claim_history":state["claim_history"],"claim_target":state["claim_target"]} if state.get("claim_history") else {}),
        **({"material_skill_record":state["material_skill_record"]} if state.get("material_skill_record") else {}), **material_context)
    prior = {review.document_id: review for review in state["suggestions"]}
    updated["suggestions"] = [prior.get(review.document_id, review) for review in updated["suggestions"]]
    return updated


def render_candidate_tools(state, harness, cutoff, material_context, api_key, base_url, model):
    audit = state["search_audit"]
    if audit.get("mode") != "online" or not audit.get("candidates"):
        return state
    if audit.get("ranking", {}).get("version") != RANKING_VERSION:
        upgraded = deepcopy(audit)
        annotate_origins(upgraded["candidates"], upgraded, state["features"])
        upgraded["candidates"] = rank_candidates(state["features"], upgraded["candidates"], harness.lexicon, upgraded.get("expansion"))
        for index, item in enumerate(upgraded["candidates"], 1):
            item["rank"] = index
        upgraded["ranking"] = {"version": RANKING_VERSION, "mode": "rules", "status": "ok",
            "metadata_reviews": {}, "note": "按当前规则重新排序已有记录，没有重新检索或调用模型"}
        state = rerun_with_evidence(state, harness, cutoff, material_context, audit=upgraded)
        st.session_state["novelty_state"] = state
        audit = state["search_audit"]
    candidates = audit["candidates"]
    by_id = {c["publication_id"]: c for c in candidates}
    max_selected = min(MAX_CANDIDATES, max(1, MAX_FEATURE_PAIRS // len(state["features"])))
    with st.expander("改善候选排序与补充相关文献"):
        st.caption("规则排序已检查部件、关系及目录/索引等提及语境；分数仍是线索。可让模型判断所选标题/摘要，再追加正文验证。")
        if st.button("按当前规则重新排序"):
            updated = deepcopy(audit)
            updated["candidates"] = rank_candidates(state["features"], updated["candidates"], harness.lexicon, updated.get("expansion"))
            for index, item in enumerate(updated["candidates"], 1):
                item["rank"] = index
            updated["ranking"].update(mode="rules", status="ok", metadata_reviews={})
            state = rerun_with_evidence(state, harness, cutoff, material_context, audit=updated)
            st.session_state["novelty_state"] = state
            audit = state["search_audit"]
        selected = st.multiselect("发送给模型判断相关性的候选", sorted(by_id),
            default=[c["publication_id"] for c in candidates[:max_selected]], max_selections=max_selected,
            format_func=lambda key: f"{key} · {by_id[key]['title']}")
        payload = [{"publication_id": key, "contexts": metadata_contexts(by_id[key])} for key in selected]
        signature = hashlib.sha256(json.dumps([sorted((key, metadata_input_hash(state["features"], by_id[key])) for key in selected),
            base_url.strip(), model.strip()], ensure_ascii=False).encode()).hexdigest()[:20]
        st.json({"features": [f.__dict__ for f in state["features"]], "candidates": payload}, expanded=False)
        consent = st.checkbox("我确认将技术要素和选定候选的标题摘要发送到模型判断相关性",
                              key=f"candidate-model-consent-{signature}")
        if st.button("模型判断候选相关性并重新排序", disabled=not (selected and consent and api_key.strip())):
            try:
                with st.spinner("判断技术关系，校验标题/摘要引文并重新排序…"):
                    result = model_judge_candidates(state["features"], [by_id[key] for key in selected],
                        LLMClient(api_key, base_url, model), consent)
                updated_audit = deepcopy(audit)
                ranking = updated_audit.setdefault("ranking", {})
                ranking.setdefault("history", []).append(result)
                ranking.update(status=result["status"], mode="model_metadata" if result["status"] == "ok" else "rules",
                               metadata_reviews=result["reviews"])
                updated_audit["candidates"] = rank_candidates(state["features"], updated_audit["candidates"],
                    harness.lexicon, updated_audit.get("expansion"), result["reviews"])
                for index, candidate in enumerate(updated_audit["candidates"], 1):
                    candidate["rank"] = index
                state = rerun_with_evidence(state, harness, cutoff, material_context, audit=updated_audit)
                st.session_state["novelty_state"] = state
                audit = state["search_audit"]
                if result["status"] == "error":
                    st.error("候选模型判断失败，已回退规则排序：" + result.get("error", "未取得有效结果"))
                st.json(result["cost"], expanded=False)
            except (RuntimeError, ValueError, PermissionError) as exc:
                st.error(str(exc))
        existing = {d.id for d in state["documents"]}
        available = {c["publication_id"]: c for c in audit["candidates"] if c["publication_id"] not in existing}
        default = [key for key, value in available.items() if (value.get("metadata_relevance") or {}).get("relevance") != "unrelated"][:2]
        selected_more = st.multiselect("选择要补充正文的候选", list(available), default=default, max_selections=5,
            format_func=lambda key: f"{key} · {available[key]['title']}")
        st.caption("补充正文不重新检索、不丢弃已有快照和有效复核；每批最多5篇，总计最多20篇。未知候选仍可选择。")
        if st.button("补充所选文献正文", disabled=not selected_more or len(existing) + len(selected_more) > 20):
            try:
                with st.spinner("读取所选Google Patents正文并保留来源…"):
                    docs, updated_audit = fetch_additional_documents(state["features"], state["documents"], audit,
                        selected_more, fetch_google_document)
                    state = rerun_with_evidence(state, harness, cutoff, material_context, audit=updated_audit, documents=docs)
                st.session_state["novelty_state"] = state
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
    return state


def render_increment(state):
    summary = state.get("retrieval_increment", {})
    if not summary.get("rows"):
        return
    st.subheader("扩展新增文献与相关性")
    st.caption(summary["scope"])
    if not summary["baseline_complete"]:
        st.warning("原词查询未全部完成或范围不完整，扩展独有候选的增量归因尚未确定。")
    st.write(f"扩展独有候选：{len(summary['expansion_only_ids'])}；模型元数据判为相关：{len(summary['new_metadata_related_ids'])}；"
             f"正文复核判为相关：{len(summary['observed_new_body_related_ids'])}。相关不等于完整披露。")
    st.dataframe([{"公开号": r["publication_id"], "来源": r["origin"], "扩展独有": r["expansion_only_observed"],
        "增量可归因": r["attributable_increment"], "元数据判断": r["metadata_relevance"], "正文判断": r["body_relevance"],
        "正文来源": (r.get("body_review") or {}).get("source", "未复核"), "正文状态": r["fetch_status"]}
        for r in summary["rows"]], hide_index=True, width="stretch")
