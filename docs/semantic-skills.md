# 专利预筛六个业务技能与独立语义评估

当前架构包含主入口 patent-precheck、六个业务子skill、独立评估 patent-semantic-eval，共八份技能定义。
LangGraph底层位于modules/precheck_graph.py，LlamaIndex阅读线索位于modules/evidence_retriever.py；旧数值工具位于engineering/legacy/，不再保留框架命名的Python skill入口。

|业务技能|实现|输出|
|---|---|---|
|材料整理|skills/material_preparation_skill.py|带材料支持的要素、未知，或明确标记用户确认输入|
|术语扩展|skills/synonym_expansion_skill.py|语境关系、候选处理状态、保护限定的查询变体|
|候选检索|skills/candidate_search_skill.py|实际查询、原文快照、失败与范围|
|证据对照|skills/evidence_alignment_skill.py|逐项支持/缺口/未知、原文和同一方案依据|
|新颖性判断|skills/novelty_assessment_skill.py|原有四类有边界业务结论|
|报告交付|skills/report_delivery_skill.py|忠于证据的JSON/Markdown及执行记录|

主入口为 skills/precheck_skill.py，配置依赖并调用底层状态图。每步调用实际业务接口，并生成含输入、实际输出、来源及哈希的 skill_records；未完成模型对照的阅读线索保持partial。
材料对话整理仍可由agent读取附件后保存；prepare_with_model只接收实际已提取文本，不假定附件已解析。

## 固定六维规范

|维度|检查|
|---|---|
|术语与对象|语境等义、上下位、相关概念、指称对象|
|结构与关系|连接、控制方向、施受角色、输入输出和组合|
|逻辑与限定|否定、条件、范围、顺序、可选及技术数值/单位|
|语境与指称|指代、同一方案、引述、背景、假设与已披露内容|
|功能与效果|功能限定与技术手段；相同目的不能补足结构缺口|
|评价与表达|评价和技术限定的混合；不按词表删除修饰语|

这些是任务检查维度，不声称语言学文献规定六层理论。六维无权重、不累加；必需维度由各技能职责指定，其他维度不适用也须说明。

独立裁判输出 findings（dimension/status/basis/citations/counterexample/next_action）、activated_dimensions、verdict、summary。上下文记录须返回动态必查维度，不能跳过已触发项。
status为satisfied/violated/undetermined/not_applicable；有违反则needs_revision，无违反但存在未定则pending_adjudication，其余acceptable。
每个确定判断同时引用skill_output和原始依据；引文逐字回查。裁判对语义的解释仍是模型意见，程序校验不能证明其正确。

执行状态与验收结论分开。技能未完成则不调用裁判；调用失败不接纳半成品；整个业务链未全部完成则不生成整体质量结论。已完成技能的有效个别意见仍保留。
不计算score、阈值、概率、召回率、精确率、MRR或通过率。内部检索排序和成本仅为运行记录，送入裁判前移除相应字段。

## 使用

网页报告下方展开独立语义评估，预览完整外发内容并确认后执行；每个已完成技能单独请求。沿用模型设置但独立调用，不自动发送附件或读取其他案件。裁判缓存绑定实际输入、输出及模型设置。

`python tools/run_semantic_eval.py --records 已导出的证据JSON --output-dir 新目录` 默认仅生成评估计划，不调用模型。
显式添加 `--run-model --confirm-external` 才调用配置的LLM_API_KEY/LLM_BASE_URL/LLM_MODEL；可用 `--references` 读取独立参考，先保存待评输入再读取参考。

engineering/legacy/basic_contracts.py与旧指标函数只用于历史工程；tools/check_semantic_contract.py为独立离线契约入口。报告统一由skills/report_delivery_skill.py组装，modules/novelty_report.py只生成基础载荷和正文。历史报告不重写。
新测试验证执行绑定、裁判正负对照、六维完整性、引文和失败处理，模拟响应不证明实际模型专业准确率。

## 上下文与跨步骤验收

六个专用合同及权利要求版本协议集中在[skill-contexts.md](skill-contexts.md)；案例共性问题见[application-baselines.md](application-baselines.md)。全部步骤完成后增加一次独立的跨skill语义核验，其输入同样预览，结果进入整体裁决。绑定冲突不能由模型接受覆盖。旧记录缺少上下文时只按其实际内容评步骤，不补造跨步骤验收。
