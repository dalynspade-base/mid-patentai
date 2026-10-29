# 原文阅读线索与逐项语义对照

证据对照业务入口为 `skills.evidence_alignment_skill`。`modules.evidence_alignment.locate_evidence` 使用 `modules.evidence_retriever.PatentRetriever` 为每篇文献生成阅读线索；LlamaIndex 与本地词典/TF-IDF 只安排阅读，不自动确认技术披露。

用户确认外发后，模型逐篇检查全部输入要素、基准日和该篇完整快照，可纠正线索或使用未命中段落。每个要素保留逐字引文、段落位置、方案编号和语义依据，说明支持全部限定、矛盾或缺口。程序校验覆盖、引文与输入绑定；同一方案和充分公开仍需来源支持。

否定、条件、连接方向、串并联、顺序、数值和单位均属于技术限定。不同文献及互斥方案不能拼接；引文真实不等于能够支持结论。未完成逐项复核的技能记录保持 partial。

主入口 `skills.precheck_skill.NoveltyHarness` 使用底层 `modules.precheck_graph` 编排。历史候选辅助函数保留在该底层模块供工程回归使用，不是额外的业务 skill。报告统一由 `skills.report_delivery_skill` 组装，格式化模块不反向调度 skill。

独立语义裁判按[六维协议](semantic-skills.md)检查实际对照结果。原有模拟响应测试只验证程序行为，不能证明专业准确率。

[整理前统一对照说明与验证记录](history/pre-1.0.0/model-evidence-alignment.md)。
