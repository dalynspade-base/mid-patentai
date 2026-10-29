---
name: patent-evidence-alignment
description: 逐篇理解专利原文，建立完整技术限定与原文证据的对应关系，核查条件、指代和方案归属；不跨文献拼接。
---

# 证据对照

`skills.evidence_alignment_skill.locate` 提供本地阅读线索；`review` 在已批准外发后逐篇调用模型检查全部快照。没有命中或低分段落仍须核查。

取得同一 `patent_context` 中的基准日、范围、目标版本和完整继承限定；逐篇结合定义、相邻语境、实施例归属及交叉引用。跨段落组合须指出文献自身的连接依据，必要附图未取得则保留未知，不靠测量示意图推定参数。专用合同及[同类争议底线](../../../docs/application-baselines.md)按需读取。

逐项给出 disclosed/not_found/unknown、逐字引文、位置、方案编号和技术依据。相似名词和引文存在本身不证明披露；重点核查关系方向、否定、条件、可选项及互斥实施例。

功能、效果和评价修饰结合语境解释，不能一律删除或据效果反推结构。未知不转为缺失，不把不同方案统一编号。确认同一方案、上下文与充分公开时须给原文依据。

模型、候选和历史人工来源分开；未完成全部有效逐项复核，技能记录保持 partial。由独立裁判按[六维规范](../../../docs/semantic-skills.md)验收。

查询策略及预算由候选检索skill与[检索协议](../../../docs/search-evidence.md)维护；正文对照按[模型证据协议](../../../docs/model-evidence-alignment.md)。本skill不重复管理查询或术语审核。
