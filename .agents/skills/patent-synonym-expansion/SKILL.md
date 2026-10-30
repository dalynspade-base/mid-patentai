---
name: patent-synonym-expansion
description: 在具体技术语境中生成和审核中英术语、译名与缩写检索变体，保留原始技术限定；用于预筛查询扩展。
metadata:
  display_name: 术语扩展
  required_dimensions: terms_objects,logic_constraints,context_reference
---

# 术语扩展

## 上下文

- 确认要素及其完整关系
- 发明描述、技术领域、专门定义（有则提供）
- 保护术语、扩展词的关系与用途

共享本轮基准日、CN/Google Patents 范围、完整限定、目标版本/引用路径和真实上游记录；缺口保留未知。[共享协议](../../../docs/skill-contexts.md)按需读取。

## 核验

将候选放回原关系中比较对象、范围和机制；等义、上下位、相关分别处理；检索有用不等于可替换。

## 边界

只扩展检索表达，不改写权利要求，不判技术披露或法律等同。

## 执行与交付

用 `modules.patent_tools.expand_terms` 组织查询变体；需要模型候选时调用 `modules.synonym_expansion.generate_llm_candidates`。保护原词、专名、否定、方向及单位；按语境区分等义、上下位、相关与未知。候选须明确审核，不能直接改写目标限定。详见[检索扩展协议](../../../docs/synonym-expansion.md)。

由工具保留实际输入/输出、出处和执行状态；Python 函数调用不等于模型已经阅读材料。交给下游时传递限定、未知、范围和来源。独立验收按 [六维规范](../../../docs/semantic-skills.md)，不自行评分或补造参考。
