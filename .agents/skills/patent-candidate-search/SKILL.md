---
name: patent-candidate-search
description: 根据技术要素与审核后的术语变体检索 Google Patents，并解释候选技术相关性和阅读优先级；不判断完整披露。
metadata:
  display_name: 候选检索
  required_dimensions: terms_objects,structure_relations,context_reference
---

# 候选检索

## 上下文

- 当前要素、基准日与来源范围
- 实际查询、扩展来源、获取失败和未读记录

共享本轮基准日、CN/Google Patents 范围、完整限定、目标版本/引用路径和真实上游记录；缺口保留未知。[共享协议](../../../docs/skill-contexts.md)按需读取。

## 核验

查询是否表达关键关系；同名提及与相关披露分开，元数据线索与正文分开；说明实际范围，不推定全网覆盖。

## 边界

返回候选不证明已披露；未找到、未读取或失败不证明不存在。

## 执行与交付

用 `modules.patent_tools.search_candidates` 检索 Google Patents 或接收显式原文快照。保存实际查询、失败及未读范围；显式空快照不触发网络。背景中真实技术描述可以相关，广告或同名提及不自动相关。排序仅安排阅读。查询策略见[检索协议](../../../docs/search-evidence.md)。

由工具保留实际输入/输出、出处和执行状态；Python 函数调用不等于模型已经阅读材料。交给下游时传递限定、未知、范围和来源。独立验收按 [六维规范](../../../docs/semantic-skills.md)，不自行评分或补造参考。
