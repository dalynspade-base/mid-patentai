# 候选排序与证据价值

当前候选检索入口是 `skills.candidate_search_skill`。`modules.novelty_search` 记录实际查询与正文获取，`modules.candidate_ranking` 安排有界阅读顺序，`modules.candidate_relevance` 区分元数据线索和正文复核。

同名词、标题重合或排序靠前仅是阅读提示。候选应结合对象与技术关系判断：背景中的真实技术描述仍可能相关；目的相同不能补足技术手段，正文未读取和获取失败必须保留未知。来源快照与在线检索范围分别记录。

独立语义评估读取查询、实际候选、正文来源和失败边界，检查对象、关系、语境及其他适用维度，给出原文依据与修订动作。新增文献数量及名次改变不作为语义质量指标。

内部排序与成本字段只作运行记录，不能用作新颖性阈值，也不输入语义裁判作为质量依据。历史 Recall/Precision 对照保留在 `engineering/legacy/synonym_eval.py`，现行入口见[语义协议](semantic-skills.md)。

[整理前排序说明与历史结果](history/pre-1.0.0/candidate-ranking.md)。
