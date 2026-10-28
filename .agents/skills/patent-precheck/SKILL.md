---
name: patent-precheck
description: 接手想法、附件或已保存案件，调度六个业务子技能完成有原文依据和未知边界的专利新颖性预筛，并衔接独立语义评估。
metadata:
  version: "2.0.0-six-business-skills"
---

# 专利预筛主入口

沿用当前材料、案件和用途，按实际进度调度以下子技能；用户已说明的内容不重复询问。程序入口为 `skills.precheck_skill.NoveltyHarness`，底层状态图不作为额外业务 skill。

| 阶段 | 读取技能 | 交接内容 |
|---|---|---|
| 材料整理 | [patent-material-preparation](../patent-material-preparation/SKILL.md) | 带材料出处的要素、未知与整理版本 |
| 术语扩展 | [patent-synonym-expansion](../patent-synonym-expansion/SKILL.md) | 原词、候选关系、保护限定及查询变体 |
| 候选检索 | [patent-candidate-search](../patent-candidate-search/SKILL.md) | 实际查询、快照、失败及范围 |
| 证据对照 | [patent-evidence-alignment](../patent-evidence-alignment/SKILL.md) | 逐项原文、完整限定和同一方案依据 |
| 新颖性判断 | [patent-novelty-assessment](../patent-novelty-assessment/SKILL.md) | 有边界的预筛结论和证据缺口 |
| 报告交付 | [patent-report-delivery](../patent-report-delivery/SKILL.md) | 统一生成的正文、来源和实际执行记录 |

实际输出保留绑定输入、输出和来源的 `skill_records`。仅有阅读线索时不冒称完成证据对照；材料、模型复核、历史人工记录分别标明来源。原始附件、case.json、旧整理和报告不覆盖，材料具体读取与保存协议由材料子技能维护。

业务输出冻结后，按需调用 [patent-semantic-eval](../patent-semantic-eval/SKILL.md)，以术语与对象、结构与关系、逻辑与限定、语境与指称、功能与效果、评价与表达逐技能核验。验收只给可接受、需修订或待裁决及原文依据，未完成/失败另列；不得自行生成专业参考。实际模型外发前展示并确认待发送内容。

## 范围与收束

当前范围为 CN 新颖性预筛与 Google Patents。普通输入按整套确认要素；权利要求模式按[明确版本和引用路径](../../../docs/skill-contexts.md)展开父项限定，`run_claims`逐目标批量运行，不合并兄弟分支。各步骤保持同一基准日、范围、目标及上游绑定；衔接冲突先退回重建。模型语义验收还须核查未知、限定和结论有没有在交接中变义。不扩展为授权、侵权或 FTO 判断。

按相关领域读取[申请争议底线](../../../docs/application-baselines.md)，用当前关系、限定和证据条件比对适用性，不建专门争议案件库，不将未授权状态当作新颖性否定。查询预算、模型外发、原文回查及案件保存协议由对应子技能和[公共规范](../../../docs/semantic-skills.md)维护。

默认对话取证最多两轮、共 8 条查询、24 次目标读取、20 分钟，失败计入；达到上限收束。此为对话取证预算，网页理论/工程策略按候选检索技能执行。记录实际查询、读取、失败和起止，无工具或时钟不伪造执行。

交付简短建议、关键证据位置、语义支持边界和影响下一步的未知，说明继续或停止条件。材料缺失不能解释为现实中不存在，模型和历史人工复核分别标明来源。只剩泛泛组合或关键关系无法核实则暂停扩大主张。

工程契约、历史规则回归和真实模型语义评估分别记录。合成回归和契约通过不能作为专业金标准；语义验收保持可接受、需修订、待裁决以及未执行/失败边界。
