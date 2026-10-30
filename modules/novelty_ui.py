"""新颖性界面：检索/粘贴原文 → 候选对照 → 大模型逐项复核 → 有边界的预筛报告。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

from modules.google_patent_documents import manual_google_document
from modules.llm_client import LLMClient
from modules.material_cases import reference, save_result
from modules.material_ui import cases_root, render_material_intake
from modules.novelty_matching import SynonymLexicon
from modules.novelty_review import MODEL_REVIEW_PROMPT_VERSION, model_review_payload
from modules.novelty_report import to_markdown
from modules.patent_tools import deliver_report
from modules.synonym_ui import render_synonym_expansion
from modules.candidate_ui import render_candidate_tools, render_increment
from modules.candidate_relevance import valid_body_review, model_endpoint
from modules.harness import NoveltyHarness
from modules.semantic_eval_ui import render_semantic_evaluation
from modules.patent_tools import review_evidence as run_evidence_review
from modules.claim_ui import render_claim_input
from modules.patent_context import context_for
from modules.novelty_models import parse_features
from modules.application_baselines import TOPICS


def fingerprint(*items: str) -> str:
    return hashlib.sha256("\x1f".join(items).encode("utf-8")).hexdigest()[:20]


def main() -> None:
    load_dotenv(Path(__file__).parents[1] / ".env")
    st.set_page_config(page_title="Google Patents 新颖性预筛", page_icon="🔎", layout="wide")
    st.title("🔎 Google Patents 新颖性预筛")
    st.caption("专利预筛主 skill · 六个业务子 skill · 独立六维语义 eval")
    with st.sidebar:
        st.header("检索与模型设置")
        max_documents = st.slider("最多获取文献数", 1, 10, 4)
        api_key = st.text_input("模型 API Key", value=os.getenv("LLM_API_KEY", ""), type="password")
        base_url = st.text_input("模型 API 地址", value=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"))
        model = st.text_input("模型名", value=os.getenv("LLM_MODEL", "deepseek-flash"))
        with st.expander("补充领域同义词"):
            extra_synonyms = st.text_area("每行一组，用 | 分隔", placeholder="术语甲 | 同义术语 | English term")
            st.caption("用于检索召回和候选定位，不代表法律等同。优先添加具体领域、含义一致的术语。")
        if st.button("检查语义 eval 契约（离线）"):
            try:
                from modules.semantic_eval_examples import contract_demo
                with st.spinner("检查离线契约示例…"):
                    st.session_state["basic_eval_report"] = contract_demo()
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        if "basic_eval_report" in st.session_state:
            evaluation = st.session_state["basic_eval_report"]
            st.write(evaluation["summary"])
            st.caption("仅检查评估契约；没有调用语义裁判，不生成业务技能的语义验收结论。")
            st.download_button("下载基础 eval 记录", json.dumps(evaluation, ensure_ascii=False, indent=2),
                               "basic_eval.json", "application/json")

    lexicon = SynonymLexicon()
    if extra_synonyms.strip():
        try:
            groups = [[word.strip() for word in line.split("|") if word.strip()]
                      for line in extra_synonyms.splitlines() if line.strip()]
            lexicon = SynonymLexicon(lexicon.groups + groups)
        except ValueError as exc:
            st.error(str(exc))
            return
    render_material_intake()
    invention = st.text_area("发明描述", key="invention_input", placeholder="描述完整技术方案；保留结构、连接关系、步骤、条件和数值。", height=110)
    claim_selection=render_claim_input()
    if claim_selection and not claim_selection["ready"]:
        return
    feature_text = st.text_area("确认待判断的技术要素（每行一个）", height=130,
                                key="feature_text_input",
                                placeholder="压力传感器\n压力传感器连接控制器\n控制器通过无线通信模块发送数据")
    if claim_selection:
        feature_text="\n".join(f.text for f in claim_selection["features"])
        st.caption("本轮采用上方选定权利要求的继承限定；普通要素编辑框不改变此目标。")
    cutoff = st.date_input("判断基准日", value=datetime.now(ZoneInfo("Asia/Shanghai")).date()).isoformat()
    with st.expander("方案语境与申请底线"):
        technical_field=st.text_input("具体技术领域（可选）")
        baseline_topics=st.multiselect("相关技术类型",list(TOPICS),default=["general"],format_func=lambda k:TOPICS[k])
        functional=st.checkbox("方案包含功能、作用机制或效果限定")
    try:
        ui_features=claim_selection["features"] if claim_selection else (parse_features(feature_text) if feature_text.strip() else [])
        context_seed={"technical_field":technical_field or "未指定","baseline_topics":baseline_topics,
                      "semantic_focus":["functions_effects"] if functional else []}
        if claim_selection:
            context_seed["target"]=claim_selection["target"]
        patent_context=context_for(ui_features,cutoff,context_seed,invention=invention)
    except ValueError as exc:
        st.error(str(exc))
        return
    expansion = render_synonym_expansion(feature_text, lexicon, api_key, base_url, model,
        features_override=ui_features,patent_context=patent_context)
    query = st.text_input("Google Patents 检索词（可选）")
    st.caption("留空时原要素优先，再发送审核通过的扩展；理论类最多8条、工程类最多16条，每条400字，不截断。自定义词保持指定范围。")
    st.caption("普通输入按整套确认要素预筛；权利要求模式按选定版本和引用路径分别比对。基准日及优先权适用性需核验。")
    st.info("检索词发送到 Google Patents。分别确认并点击生成术语或模型复核后，对应的要素/术语表或原文/候选才发送到模型。")
    prepared = st.session_state.get("prepared_material_features")
    material_context = {"material_unknowns": list(prepared.get("unknowns", [])) if prepared else [],
                        "material_warnings": list(prepared.get("warnings", [])) if prepared else []}
    if prepared and prepared["feature_text"] != feature_text:
        material_context["material_warnings"].append("当前要素已修改，原整理出处不能自动证明新要素的材料支持。")
    material_identity = {"case": st.session_state.get("active_material_case"),
                         "preparation": prepared.get("preparation_ref") if prepared else None,
                         **material_context}
    material_skill_context={"material_original":invention,"patent_context":patent_context}
    if claim_selection:
        material_skill_context.update(claim_history=claim_selection["history"],claim_target=claim_selection["target"]["id"])
    if not claim_selection and prepared and prepared["feature_text"]==feature_text and prepared.get("skill_record"):
        material_skill_context["material_skill_record"]=prepared["skill_record"]
    signature = fingerprint(invention, feature_text, cutoff, query, str(max_documents),
                            json.dumps(lexicon.groups, ensure_ascii=False),
                            json.dumps(expansion, ensure_ascii=False, sort_keys=True),
                            json.dumps(patent_context, ensure_ascii=False, sort_keys=True),
                            json.dumps(material_identity, ensure_ascii=False, sort_keys=True))
    harness = NoveltyHarness(lexicon=lexicon, max_documents=max_documents)
    state = st.session_state.get("novelty_state") if st.session_state.get("novelty_signature") == signature else None

    if st.button("1 · 检索 Google Patents 并定位证据", type="primary", disabled=not feature_text.strip()):
        st.session_state.pop("novelty_state", None)
        try:
            with st.spinner("扩展同义词、检索文献并读取原文…"):
                state = harness.run(feature_text=feature_text, query=query, cutoff=cutoff, expansion=expansion, **material_context, **material_skill_context)
            st.session_state["novelty_state"] = state
            st.session_state["novelty_signature"] = signature
            st.session_state["review_epoch"] = 0
        except (RuntimeError, ValueError) as exc:
            st.error(str(exc))

    with st.expander("网页无法访问时：粘贴 Google Patents 原文快照"):
        manual_url = st.text_input("Google Patents 单篇链接")
        manual_title = st.text_input("文献标题")
        manual_pub = st.text_input("公开日期 YYYY-MM-DD")
        manual_filing = st.text_input("申请日期（可选）")
        manual_priority = st.text_input("优先权日期（可选）")
        manual_body = st.text_area("原文（空行分段，不要只粘贴搜索摘要）", height=150)
        manual_complete = st.checkbox("我确认已粘贴待比对的完整相关权利要求和说明书，保留了上下文限定")
        if st.button("加入人工原文快照", disabled=not feature_text.strip()):
            try:
                document = manual_google_document(manual_url, manual_title, manual_pub, manual_body,
                                                  manual_filing, manual_priority, manual_complete)
                documents = [doc for doc in (state or {}).get("documents", []) if doc.id != document.id] + [document]
                state = harness.run(feature_text=feature_text, query=query, cutoff=cutoff, documents=documents,
                                    reviews=[r for r in (state or {}).get("reviews", []) if r.document_id != document.id],
                                    errors=(state or {}).get("errors", []),
                                    **({"search_audit": state["search_audit"]} if state and "search_audit" in state else {}),
                                    **material_context, **material_skill_context)
                st.session_state["novelty_state"] = state
                st.session_state["novelty_signature"] = signature
                st.session_state["review_epoch"] = st.session_state.get("review_epoch", 0) + 1
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
    if state is None:
        st.caption("可直接人工录入 Google Patents 原文，无需配置模型密钥。修改输入后需重新建立对照。")
        return

    st.divider()
    state = render_candidate_tools(state, harness, cutoff, material_context, api_key, base_url, model)
    st.subheader("2 · 查看原文与候选对照")
    audit = state["search_audit"]
    with st.expander("本轮检索范围与记录"):
        if audit["mode"] == "provided_snapshots":
            st.write("仅比对提供的快照；本轮未执行在线检索，不能推断检索覆盖。")
        else:
            st.write("在线检索记录：所有计划内查询先执行，再按独立文献预算读取正文。先用部件/关系与提及语境规则排序；可补充模型元数据判断，正文相关性另行复核。")
            st.dataframe([{"查询": r["id"], "要素": ", ".join(r["feature_ids"]) or "自定义范围",
                           "实际检索词": r["query"], "状态": r["status"], "返回数": r["returned_count"],
                           "开始": r["started_at"], "结束": r["finished_at"], "错误": r["error"]}
                          for r in audit["queries"]], hide_index=True, width="stretch")
            for skipped in audit.get("skipped", []):
                st.warning(f"未发送：{', '.join(skipped['feature_ids']) or '自定义变体'} · {skipped['reason']}")
            st.dataframe([{"公开号": c["publication_id"], "标题": c["title"], "链接": c["url"],
                           "来自查询": ", ".join(c["query_ids"]), "召回来源": c.get("retrieval_origin", "unknown"),
                           "词汇重合提示": c["lexical_hint"]["score"], "规则排序分": c.get("ranking_hint", {}).get("score"),
                           "仅提及线索": c.get("ranking_hint", {}).get("mention_only_hint"),
                           "模型元数据相关性": (c.get("metadata_relevance") or {}).get("relevance", "unknown"),
                           "正文状态": c["fetch_status"], "获取错误": c["fetch_error"]}
                          for c in audit["candidates"]], hide_index=True, width="stretch")
    for error in state.get("errors", []):
        st.warning(error)
    for doc in state["documents"]:
        with st.expander(f"{doc.id} · {doc.title}"):
            st.link_button("打开 Google Patents 来源", doc.url)
            st.write(f"公开日：{doc.publication_date or '未取得'}；来源类型：{doc.source_kind}")
            for section in doc.sections:
                location = section.source_location or "旧版快照内部编号（非原文权利要求号）"
                st.text(f"[{section.id}] {location}：{section.text}")
    if state.get("hits"):
        with st.expander("本地检索阅读线索（模型会检查全部原文）"):
            st.dataframe(state["hits"], hide_index=True, width="stretch")
    st.caption("本地检索帮助定位段落；模型负责理解技术含义、限定与证据支持关系。匹配分数不是新颖性概率。")

    st.subheader("3 · 模型建立要素—原文证据对照并复核")
    st.caption("模型结合全部原文建立逐项对应关系，解释证据如何支持要素及全部限定，再核查相关性、同一方案和充分公开。")
    options = {doc.id: doc for doc in state["documents"]}
    selected = st.multiselect("待逐项复核的文献", list(options), default=list(options))
    candidates_by_id = {review.document_id: review for review in state["suggestions"]}
    payloads = [model_review_payload(state["features"], options[key], cutoff, candidates_by_id.get(key), state.get("hits", []),
                                   patent_context=state.get("patent_context"))
                for key in selected]
    consent_payload = json.dumps(payloads, ensure_ascii=False, sort_keys=True)
    review_signature = fingerprint(consent_payload, base_url.strip(), model.strip())
    st.caption("每篇一次模型请求，发送全部要素、该篇全部快照及本地定位线索；未命中或低分段落仍须检查。单篇超过5万字符时整批拒绝。")
    with st.expander("查看本轮模型将收到的内容"):
        st.json(payloads)
    consent = st.checkbox("我确认将上述要素、原文和候选对照发送到此模型服务",
                          key=f"review-consent-{review_signature}")

    def clear_review_result(selected_ids=None):
        # 只撤销本轮重试的文献；其他输入有效的正文复核继续保留。
        retained = [] if selected_ids is None else [r for r in state.get("reviews", []) if r.document_id not in selected_ids]
        refreshed=harness.run(features=state["features"],cutoff=cutoff,documents=state["documents"],reviews=retained,
            errors=state.get("errors",[]),search_audit=state["search_audit"],**material_context,**material_skill_context)
        state.update(refreshed)
        st.session_state.pop("model_review_signature", None)

    by_doc = {doc.id: doc for doc in state["documents"]}
    invalid = {r.document_id for r in state.get("reviews", []) if
        not valid_body_review(r, state["features"], by_doc.get(r.document_id), cutoff,patent_context=state.get("patent_context")) or
        (r.review_method == "model" and (r.review_model != model.strip() or r.review_endpoint != model_endpoint(base_url.strip())
                                       or r.review_prompt_version != MODEL_REVIEW_PROMPT_VERSION))}
    if invalid:
        clear_review_result(invalid)
        st.info("部分正文复核的输入、模型设置或证据对照流程已改变，旧记录已失效，请重新复核。")
    st.caption("文献选择仅决定本次发送哪些正文；此前输入有效的复核继续保留。新增文献没有复核记录时保持未知。")

    if st.button("调用大模型逐项复核并预筛", type="primary",
                 disabled=not (consent and selected and api_key.strip())):
        clear_review_result(set(selected))
        try:
            candidates = state["suggestions"]
            with st.spinner("逐篇调用模型复核并校验引文、要素和段落覆盖…"):
                reviews, _ = run_evidence_review(state["features"], [options[key] for key in selected], cutoff,
                    LLMClient(api_key, base_url, model), consent, candidates, evidence_hits=state.get("hits", []),
                    context=state.get("patent_context"),upstream=state.get("skill_records", []))
                updated = harness.run(features=state["features"], cutoff=cutoff,
                    documents=state["documents"], reviews=list({r.document_id: r for r in list(state.get("reviews", [])) + reviews}.values()), errors=state.get("errors", []),
                    search_audit=state["search_audit"], **material_context, **material_skill_context)
            updated["suggestions"] = candidates
            state = updated
            st.session_state["novelty_state"] = state
            st.session_state["model_review_signature"] = review_signature
        except (RuntimeError, ValueError, PermissionError) as exc:
            st.error(str(exc))

    reviews_by_id = {review.document_id: review for review in state.get("reviews", [])}
    for doc in state["documents"]:
        with st.expander(f"本地定位提示（供模型独立核查） · {doc.id}"):
            st.dataframe([asdict(row) for row in candidates_by_id[doc.id].rows],
                         hide_index=True, width="stretch")
        review = reviews_by_id.get(doc.id)
        if not review:
            st.caption(f"{doc.id}：尚无有效模型复核。未选文献仍作为未复核缺口保留。")
            continue
        st.markdown(f"**模型建立的要素—原文证据对照 · {doc.id}**")
        st.caption(f"模型：{review.review_model}；复核时间：{review.reviewed_at}；非人工确认。")
        feature_texts = {feature.id: feature.text for feature in state["features"]}
        st.dataframe([{"feature_text": feature_texts[row.feature_id], **asdict(row)} for row in review.rows], hide_index=True, width="stretch",
            column_config={"feature_text": "技术要素", "status": "披露状态", "quote": "逐字原文引文", "section_id": "原文位置",
                           "scheme_id": "同一技术方案编号", "basis": "模型逐项复核说明", "origin": "来源"})
        st.write(f"技术相关性：{review.relevance}；{review.relevance_basis}")
        st.write(f"同一技术方案：{review.same_scheme_confirmed}；{review.same_scheme_basis}")
        st.write(f"充分公开：{review.enabling_disclosure_confirmed}；模型上下文完整性判断：{review.context_complete}")
        st.caption(f"上下文依据：{review.completeness_basis}；解析结构提示：{doc.parsed_complete}。")

    st.divider()
    render_increment(state)
    st.subheader("预筛结果")
    decision = state["decision"]
    st.write(f"**{decision.text}**")
    for reason in decision.reasons:
        st.write(reason)
    for warning in decision.warnings:
        st.warning(warning)
    report_context = {}
    active_case = st.session_state.get("active_material_case")
    case_ready = False
    if active_case:
        try:
            current_case = reference(cases_root(), active_case["case_id"])
            if current_case != active_case:
                raise ValueError("材料版本已变化，请重新保存并建立对照。")
            report_context["material_case"] = current_case
            prepared = st.session_state.get("prepared_material_features")
            if prepared:
                report_context["feature_preparation"] = prepared
                report_context["feature_preparation_matches_input"] = prepared["feature_text"] == feature_text
            case_ready = True
        except (OSError, ValueError, KeyError, TypeError) as exc:
            st.error(str(exc))
            return
    payload, _ = deliver_report(state, invention, cutoff, lexicon.groups, **report_context)
    semantic_result=render_semantic_evaluation(payload["skill_records"],api_key,base_url,model)
    if semantic_result:
        payload["semantic_evaluation"]=semantic_result
    st.session_state["last_generated_report"] = payload
    st.download_button("下载新颖性 Markdown 报告", to_markdown(payload), "novelty_report.md", "text/markdown")
    st.download_button("下载证据快照与判断记录 JSON", json.dumps(payload, ensure_ascii=False, indent=2),
                       "novelty_evidence.json", "application/json")
    if case_ready and st.button("保存本轮结果到案件"):
        try:
            saved_path = save_result(cases_root(), active_case["case_id"], payload, to_markdown(payload))
            st.success(f"本轮结果已单独保存：{saved_path.name}。原始材料和以前的结果保持不变。")
        except (OSError, ValueError) as exc:
            st.error(str(exc))
    st.caption("本次未发现完整披露不等于具备新颖性或可以授权。资料范围仅为本次取得的 Google Patents 文献。")
