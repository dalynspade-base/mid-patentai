# -*- coding: utf-8 -*-
"""细则（policy）：本 test 集的"评分细则答案"从这里推导。

对照 rsi-lab 课堂作业的分工：
- rsi-lab 的 policy.py 用代码把评分细则写成唯一真值，被测阅卷 Agent 看不到；
- 本目录的 policy.py 把细则写成条文（RULES）+ 共享工具函数，score.py 按条文对账。
- label 写在各 cases JSON 的 label/expected 字段里，由这些条文推导；被测流程不得读取。

本文件只描述规则，不执行打分；打分见 score.py。
"""

from __future__ import annotations

import re

# ---- 结论与裁决的封闭集合（与项目 2.0.0 口径一致） ----

CONCLUSIONS = {
    "potential_novelty_risk": "单篇文献在同一技术方案中完整披露全部要素（预筛风险提示）",
    "no_complete_disclosure_found": "本次已复核文献中未发现一篇完整披露全部要素（不等于不存在现有技术）",
    "insufficient_evidence": "存在资料、日期、检索、要素或复核缺口，不能排除风险",
    "out_of_scope": "超出新颖性预筛范围（如 FTO/侵权/可授权性）",
}

VERDICTS = {
    "acceptable": "可接受",
    "needs_revision": "需修订",
    "pending_adjudication": "待裁决",
}

# 六维键名，与 modules/skill_contracts.DIMENSIONS 一致
DIMENSIONS = {
    "terms_objects": "术语与对象",
    "structure_relations": "结构与关系",
    "logic_constraints": "逻辑与限定",
    "context_reference": "语境与指称",
    "functions_effects": "功能与效果",
    "evaluation_expression": "评价与表达",
}

# ---- 细则条文：label 由此推导 ----

RULES = [
    {"id": "R01", "name": "引文逐字回查",
     "text": "status=disclosed 的每一行必须给出原文引文；引文按空白归一化后必须能在所引小节中逐字找到，否则该对照无效。"},
    {"id": "R02", "name": "状态枚举与 unknown 纪律",
     "text": "status 只能是 disclosed/not_found/unknown；unknown 不等于 not_found，存在 unknown 时不能得出排除风险的结论。"},
    {"id": "R03", "name": "同一方案分离",
     "text": "同一文献中的互斥实施例（不同 scheme_id）不得并成一个方案；完整要素分属不同方案时不得判完整披露。"},
    {"id": "R04", "name": "日期门",
     "text": "公开日必须早于基准日才作为普通现有技术；同日不自动在先；CN 文献申请日早于基准日而公开日晚于基准日的，按疑似抵触申请转人工，不得按公开日直接丢弃。"},
    {"id": "R05", "name": "功能不补结构",
     "text": "相同功能/效果不能补足结构与机制差异；光致转换与电致发光等不同机制不得因结果相同而视为披露。"},
    {"id": "R06", "name": "摘要可选不推翻权利要求必要限定",
     "text": "摘要中的 optionally/in some examples 是例示或层级更低的表述；权利要求中的明确限定仍按必要限定处理。"},
    {"id": "R07", "name": "术语按专利自定义边界",
     "text": "专利自定义术语（如 transparent 定义包含 translucent/tinted/frosted）按其定义边界理解，不按日常语义收窄。"},
    {"id": "R08", "name": "证据层级与引文位置",
     "text": "每个要素的披露依据必须引自正确的层级与小节：摘要概括词不能替代权利要求逐层限定；独权界限不能支持从权优选范围；每项数值/界限回到其所属权利要求。"},
    {"id": "R09", "name": "数值与单位照抄",
     "text": "数值范围、单位、工序顺序按原文照抄；无依据地怀疑笔误或改写界限属于修订项。"},
    {"id": "R10", "name": "禁止跨文献拼接",
     "text": "不得把多篇文献各自披露的要素拼成一篇完整披露；每篇按其自身方案单独判断。"},
    {"id": "R11", "name": "同族版本与日期链",
     "text": "同一申请的 A/B 公开版本是不同快照；只用后公开版本日期而忽略更早公开版本时，日期链不完整，应转补充取证，不得据此下未发现结论。"},
    {"id": "R12", "name": "背景技术不是方案披露",
     "text": "背景技术中对材料或领域的一般性列举（如广泛用于光伏电池）不构成本文献的技术方案披露；引述他人内容须区分归属。"},
    {"id": "R13", "name": "伪造证据即修订",
     "text": "引文、出处或材料限定与原文相反或原文不存在时，除业务结论外必须判 needs_revision。"},
    {"id": "R14", "name": "裁决推导",
     "text": "存在任何 violated → needs_revision；无 violated 但存在 undetermined（含诚实标注的 unknown）→ pending_adjudication；其余 → acceptable。待裁决是验收意见，不是业务结论，二者不互相替代。"},
]


def normalize(text: str) -> str:
    """空白归一化：引文核对按此口径逐字匹配（不改写任何非空白字符）。"""
    return re.sub(r"\s+", " ", text or "").strip()


def verdict_from_findings(statuses: list[str]) -> str:
    """R14：由各维度 finding 状态推导裁决。"""
    if any(s == "violated" for s in statuses):
        return "needs_revision"
    if any(s == "undetermined" for s in statuses):
        return "pending_adjudication"
    return "acceptable"
