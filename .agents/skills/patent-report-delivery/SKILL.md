---
name: patent-report-delivery
description: 将已确认输入、原文证据、模型复核和预筛结果组织成可追溯报告，保留未知、来源和适用范围。
metadata:
  display_name: 报告交付
  required_dimensions: logic_constraints,context_reference,evaluation_expression
---

# 报告交付

## 上下文

- 最终结构化业务结果与正文
- 基准日、范围、目标版本及修改摘要
- 来源、未知项与实际执行记录

共享本轮基准日、CN/Google Patents 范围、完整限定、目标版本/引用路径和真实上游记录；缺口保留未知。[共享协议](../../../docs/skill-contexts.md)按需读取。

## 核验

双向核对结构化结果和正文，保留条件、缺口、意见来源与结论边界；不把局部结论扩展到其他权利要求或版本。

## 边界

评交付内容是否忠实；保存或下载成功不作为专业验收，评估附录不递归评估自身。

## 执行与交付

用 `modules.patent_tools.deliver_report` 生成 JSON 与 Markdown；案件保存用 `modules.material_cases.save_result`，每轮另存。保持结构化结论与正文一致；模型不改称人工，未找到不改称不存在，某一权利要求/分支/版本不扩展到整件申请。附加当前绑定的评估，不将评估附录递归放入待测正文。

由工具保留实际输入/输出、出处和执行状态；Python 函数调用不等于模型已经阅读材料。交给下游时传递限定、未知、范围和来源。独立验收按 [六维规范](../../../docs/semantic-skills.md)，不自行评分或补造参考。
