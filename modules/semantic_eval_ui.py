"""Separate consent and cache binding for the independent semantic judge."""
import json
import streamlit as st
from modules.llm_client import LLMClient
from modules.skill_contracts import DIMENSIONS,SKILLS,digest
from modules.semantic_eval import RUBRIC_VERSION,VERDICTS,EXECUTION_LABELS,FINDING_LABELS,evaluation_packet,evaluation_markdown
from skills.semantic_eval_skill import run
from modules.semantic_handoff import handoff_packet


def render_semantic_evaluation(records,api_key,base_url,model):
    signature=digest({"rubric_version":RUBRIC_VERSION,"records":records,"endpoint":base_url.strip(),"model":model.strip()})
    key="semantic-evaluation-"+signature
    with st.expander("独立语义评估：逐个业务 skill 核验",expanded=False):
        st.caption("六个维度："+"、".join(DIMENSIONS.values())+"。输出可接受、需修订或待裁决，不生成数值质量指标。")
        st.caption("裁判是与业务生成分开的调用；沿用当前模型配置，明确标记模型来源，不冒称独立专家或另一模型。")
        packets=[evaluation_packet(r) for r in records]
        st.json({"evaluation_inputs":packets,"handoff_input":handoff_packet(records)},expanded=False)
        st.caption("全部步骤完成后，另行核验步骤间限定、未知和结论的传递；衔接输入也在上方预览。")
        consent=st.checkbox("我确认将上述技能输入、输出和原文发送给独立语义裁判",key="semantic-consent-"+signature)
        if st.button("逐技能运行语义 eval",disabled=not(consent and api_key.strip() and records)):
            st.session_state.pop(key,None)
            try:
                with st.spinner("独立核验业务输出与原文…"):
                    st.session_state[key]=run(records,LLMClient(api_key,base_url,model),True)
            except (ValueError,RuntimeError,PermissionError) as exc:
                st.error(str(exc))
        result=st.session_state.get(key)
        if result:
            if result.get("assessment_scope")=="individual_skills_only":
                st.caption("本次仅验收所列技能，未形成跨skill衔接验收。")
            st.write("执行状态："+EXECUTION_LABELS[result["execution"]]+"；整体裁决："+VERDICTS.get(result["verdict"],"未形成完整验收结论"))
            rows=list(result["results"])
            if result.get("handoff"):
                rows.append(dict(result["handoff"],skill_name="跨skill衔接核验"))
            for row in rows:
                st.markdown("**"+row["skill_name"]+" · "+VERDICTS.get(row["verdict"],"未生成验收结论")+"**")
                st.write(row["summary"])
                for finding in row["findings"]:
                    st.write(DIMENSIONS[finding["dimension"]]+" · "+FINDING_LABELS[finding["status"]]+"："+finding["basis"])
                    for citation in finding["citations"]:
                        st.text("["+citation["source_id"]+"] "+citation["quote"])
                    if finding["counterexample"]:
                        st.write("反例或缺口："+finding["counterexample"])
                    if finding["next_action"]:
                        st.write("后续处理："+finding["next_action"])
            st.download_button("下载逐技能语义 eval JSON",json.dumps(result,ensure_ascii=False,indent=2),"semantic_eval.json","application/json")
            st.download_button("下载逐技能语义 eval 报告",evaluation_markdown(result),"semantic_eval.md","text/markdown")
        return result
