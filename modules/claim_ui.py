"""Optional explicit claim-history input; ordinary confirmed-feature input stays available."""
import json
from pathlib import Path

import streamlit as st

from modules.claim_context import claim_targets, resolve_target, validate_history


def render_claim_input():
    enabled=st.checkbox("按独立／从属权利要求及修改版本比对")
    if not enabled:
        return None
    with st.expander("权利要求与修改历史",expanded=True):
        st.caption("导入明确的原文、引用关系和版本；多项引用分别比对。新增限定须保留完整关系，不能只填部件名。")
        sample=Path(__file__).parents[1]/"examples/claim_history.json"
        st.download_button("下载权利要求记录示例",sample.read_text(encoding="utf-8"),"claim_history.json","application/json")
        uploaded=st.file_uploader("导入权利要求记录",type=["json"],key="claim-history-upload")
        pasted=st.text_area("或粘贴权利要求记录",key="claim-history-text",height=140)
        if not uploaded and not pasted.strip():
            st.info("请导入记录后选择版本与引用路径。")
            return {"ready":False}
        try:
            history=validate_history(json.loads(uploaded.getvalue().decode("utf-8-sig") if uploaded else pasted))
            vids=[v["id"] for v in history["versions"]]
            vid=st.selectbox("采用的权利要求版本",vids,index=vids.index(history["selected_version"]))
            history["selected_version"]=vid
            targets=claim_targets(history)
            target_id=st.selectbox("当前权利要求及引用路径",[t["id"] for t in targets])
            features,target,_=resolve_target(history,target_id)
            st.write("继承路径："+" → ".join(target["path"]))
            for feature in features:
                st.write("• "+feature.text)
            if any(c["support_status"]=="basis_not_provided" for c in target["amendments"]):
                st.warning("部分修改未提供原始材料依据；本工具不确认修改合法性。")
            return {"ready":True,"history":history,"target":target,"features":features}
        except (ValueError,TypeError,KeyError,UnicodeError) as exc:
            st.error(str(exc))
            return {"ready":False}
