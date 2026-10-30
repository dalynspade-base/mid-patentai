---
name: patent-material-preparation
description: 将已读取的发明材料整理为带原文出处的技术要素与未知项；用于专利预筛的材料理解，不补造实现和效果。
metadata:
  display_name: 材料整理
  required_dimensions: terms_objects,structure_relations,logic_constraints,context_reference
---

# 材料整理

## 上下文

- 原始材料或明确的用户确认要素
- 实际读取范围、未知项
- 采用权利要求模式时的原文、版本与引用路径

共享本轮基准日、CN/Google Patents 范围、完整限定、目标版本/引用路径和真实上游记录；缺口保留未知。[共享协议](../../../docs/skill-contexts.md)按需读取。

## 核验

从输出回查来源，再从原文反查遗漏；保留关系、条件、否定及继承限定。说明书用于解释，不擅自导入未要求的实施例细节。

## 边界

不补造实现；不把附件存在称为已经读取；不自行确认修改合法性。

## 执行与交付

`modules.patent_tools.prepare_materials` 规范化已确认要素；需要模型整理实际文本时调用 `prepare_materials_with_model(sources, client, confirmed)`。保存案件整理用 `modules.material_cases.save_prepared_features`。接手已保存附件时读 [案件整理协议](references/case-preparation.md)，原件和旧整理不覆盖。

由工具保留实际输入/输出、出处和执行状态；Python 函数调用不等于模型已经阅读材料。交给下游时传递限定、未知、范围和来源。独立验收按 [六维规范](../../../docs/semantic-skills.md)，不自行评分或补造参考。
