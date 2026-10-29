---
name: patent-novelty-assessment
description: 根据有效逐项证据对照核验同一对比方案的完整公开，给出有范围限制的中国专利新颖性预筛结论。
---

# 新颖性判断

使用 `skills.novelty_assessment_skill.run`，沿用来源、日期、逐字引文、输入绑定和 CN 新颖性规则。输入为确认要素集，或入口按明确引用路径展开的权利要求目标；多项引用分支分别判断，不处理创造性或侵权。

取得目标版本、完整限定、有效复核、日期链、检索缺口及 `patent_context.rule_profile`。上游限定、基准日、范围或绑定不一致时退回重建。案例底线先比较争议关系和证据条件，分别指出新颖性、创造性、公开充分性、支持或程序问题，不用“不过审”替代具体理由；见[上下文协议](../../../docs/skill-contexts.md)。

结论须有同一对比方案共同支持全部限定的证据链，或指出具体公开缺口和不确定性。不跨文献或互斥方案拼接，不把仅相容当必然隐含，不用目的相同替代技术方案比较。

保留 potential_novelty_risk/no_complete_disclosure_found/insufficient_evidence/out_of_scope 业务结果。未发现完整披露只限本次证据，不推出可授权；材料充分时也不能无理由拒答。

交付结论、理由、证据文献和限制以及绑定执行记录；验收由独立语义评估完成，见[公共规范](../../../docs/semantic-skills.md)。
