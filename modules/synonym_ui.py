"""同义词四层流程的界面；新术语审核仅授权查询，不是人工专利结论。"""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import streamlit as st

from modules.llm_client import LLMClient
from modules.novelty_models import parse_features
from modules.synonym_expansion import PROFILES
from modules.patent_tools import expand_terms as run_synonym_tool
from modules.synonym_expansion import generate_llm_candidates


def build_expansion_plan(*args,**kwargs):
    return run_synonym_tool(*args,**kwargs)[0]


def render_synonym_expansion(feature_text, lexicon, api_key, base_url, model,*,features_override=None,patent_context=None):
    with st.expander("同义词四层扩展与检索评测", expanded=False):
        profile = st.selectbox("专利检索类型", list(PROFILES), index=1,
                               format_func=lambda key: PROFILES[key]["label"])
        policy = PROFILES[profile]
        st.caption(f"{policy['label']}：最多{policy['query_budget']}条查询，每要素最多{policy['variants_per_feature']}个变体；"
                   f"最多组合{policy['combination_width']}个独立术语。类型由你按当前技术内容选择。")
        protected = [x.strip() for x in st.text_area("保护术语（每行一个，不做替换）",
            placeholder="Kalman\n某标准编号或算法专名").splitlines() if x.strip()]
        st.caption("第一层使用预定义词典；第二层可选LLM生成中英文和缩写；第三层拦截语义漂移及限定变化。"
                   "词典外的新词需明确审核才进入查询，未审核项不自动当同义词。")
        try:
            features = features_override if features_override is not None else (parse_features(feature_text) if feature_text.strip() else [])
        except ValueError as exc:
            st.warning(str(exc))
            return None
        payload = {"features": [asdict(f) for f in features], "profile": profile,
                   "protected_terms": protected, "dictionary_groups": lexicon.groups,"patent_context":patent_context}
        signature = hashlib.sha256(json.dumps([payload, base_url.strip(), model.strip()],
                                   ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
        consent = st.checkbox("我确认将技术要素、保护术语和词典发送到模型生成扩展候选",
                              key=f"synonym-consent-{signature}")
        st.json(payload, expanded=False)
        cache_key = f"synonym-generation-{signature}"
        if st.button("生成中英文同义词候选", disabled=not (features and consent and api_key.strip())):
            st.session_state.pop(cache_key, None)
            try:
                with st.spinner("生成候选并检查术语与限定…"):
                    st.session_state[cache_key] = generate_llm_candidates(features, lexicon, profile, protected,
                        LLMClient(api_key, base_url, model), consent,patent_context=patent_context)
            except (ValueError, RuntimeError, PermissionError) as exc:
                st.error(str(exc))
        generated = st.session_state.get(cache_key)
        plan = build_expansion_plan(features, lexicon, profile, protected, generated) if features else None
        if generated:
            if generated["status"] == "error":
                st.error("候选生成失败，保留词典方案：" + generated.get("error", "未取得有效候选"))
            st.caption("本次候选生成成本（未知用量不填零）：")
            st.json(generated["cost"], expanded=False)
        pending = {c["id"]: c for c in (plan or {}).get("candidates", []) if c["status"] == "pending"}
        candidate_signature = hashlib.sha256(json.dumps((generated or {}).get("candidates", []),
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
        approved = st.multiselect("审核后允许用于查询的新增术语", list(pending),
            format_func=lambda key: f"{pending[key]['feature_id']} · {pending[key]['source']} → {pending[key]['term']} · {pending[key]['rationale']}",
            key=f"synonym-approved-{signature}-{candidate_signature}")
        approval_signature = hashlib.sha256(json.dumps([signature, approved, (generated or {}).get("candidates", [])],
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
        confirmation = st.checkbox("我已核对所选新增术语在当前要素中的技术含义和限定",
                                    key=f"synonym-approved-confirm-{approval_signature}")
        if plan and approved and confirmation:
            plan = build_expansion_plan(features, lexicon, profile, protected, generated, approved)
        if plan:
            if patent_context:
                from modules.skill_contracts import digest
                plan["context_binding"]=digest({k:patent_context[k] for k in ("features_hash","cutoff","scope","target")})
            st.dataframe(plan["candidates"], hide_index=True, width="stretch")
            st.caption(f"本轮有{len(plan['variants'])}个候选查询变体；按原要素优先、类型预算发送，多余或超长查询另行记录。")
            st.download_button("下载同义词扩展与审核记录", json.dumps(plan, ensure_ascii=False, indent=2),
                               "synonym_expansion.json", "application/json")
        st.caption("第四层在报告的独立语义评估中核验术语保真、上下位关系、限定及检索价值，不计算数值质量指标。")
        if st.button("查看术语扩展语义 eval 规范（离线）"):
            from modules.skill_contracts import DIMENSIONS,SKILLS
            st.session_state["synonym-eval-result"]={"execution":"not_executed","verdict":None,
                "dimensions":DIMENSIONS,"obligations":SKILLS["patent-synonym-expansion"]["obligations"],
                "note":"只展示规范；没有调用模型，也没有语义验收结果。"}
        if "synonym-eval-result" in st.session_state:
            st.json(st.session_state["synonym-eval-result"])
        return plan
