# 工程专利单点随机修改：真实试运行记录

## 示例与范围

基准专利：[US4968168A — Ballpoint pen with clicking](https://patents.google.com/patent/US4968168A/en)；公开日 1990-11-06。

补充对比文献：[US5944436A — Pen possible to sound](https://patents.google.com/patent/US5944436A/en)；公开日 1999-08-31。

通俗解释：转动笔帽，笔芯伸缩；弹性偏置使两个带齿的环形件配合，到位时发出咔嗒声。

按整套选定技术要素对照，不展开独立/从属权利要求。此处是具体工程方案的教学试验，不是对该专利完整保护范围的重建。

本次假设判断基准日：2026-10-19。专利是否过期不决定其公开内容能否成为现有技术。

## 随机修改

随机种子：`3862674471`；候选变更数：3；选中：`pom_tooth_crowns`。

只修改 **F6**：把环形齿冠材料从金属改为聚甲醛（POM），保留齿形与咔嗒反馈，其余五项保持不变。

| 要素 | 原方案 | 修改后 |
|---|---|---|
| F1 | 笔杆具有圆柱形连接端和锥形笔尖端。 | 保持不变 |
| F2 | 转动笔帽，使带圆珠笔尖的储墨管沿轴向伸出或缩回。 | 保持不变 |
| F3 | 空心杆外螺纹配合中间管的螺纹孔，将旋转变成有限轴向位移。 | 保持不变 |
| F4 | 压缩弹簧使固定环形啮合件压向旋转环形啮合件。 | 保持不变 |
| F5 | 固定和旋转啮合件具有环形齿冠。 | 保持不变 |
| F6 | 两环形齿冠采用金属，啮合和脱离时发出可听到的咔嗒声。 | The annular tooth crowns of the fixed and rotatable engaging members are made of polyoxymethylene (POM) polymer instead of metal and still produce audible clicking when engaging and disengaging. |

## 实际运行链路

Google Patents 真实页面 → 原文快照 → LlamaIndex 候选检索 → DeepSeek 真实对照 → 引文校验 → LangGraph 规则判断 → Markdown/JSON。

检索探针与固定两篇文献的前后对照分别记录；没有把两篇文献覆盖范围说成全库覆盖率。

在线检索探针：成功。详见 run_metadata.json。

## 原方案与修改版结果

| 版本 | 真实模型对照 | Harness 结果 |
|---|---|---|
| 原方案 | success | 证据不足，需要补充或复核 |
| 修改版 | success | 证据不足，需要补充或复核 |

**没有专业人员人工复核，所以 reviewed 始终为 false。模型对照不冒充人工确认，系统的保守结果不是自动宣告可授权。**

## 修改版逐文献对照（模型建议，待复核）

### US4968168A

| 要素 | 建议状态 | 原文位置 | 引文 |
|---|---|---|---|
| F1 | disclosed | claim-2 | an outer barrel having a cylindrical connecting first end and a conical tip second end; |
| F2 | disclosed | claim-2 | an inner tubular ink reservoir having a ball point extendable outside the second conical tip end of the outer barrel extending through the annular connector, through the biasing means; through the fixed annular engaging member, through the rotatable annular engaging member, through the rotatable hollow rod and fixed into the cap; whereby rotating the cap of the hollow rotatable rod in one direction moves the hollow rod and the rotatable engaging member to its inner limit against the biasing means of the fixed engaging member and rotating the cap in the opposite direction moves the rotatable engaging member and the hollow rod to its limit in the same direction as the force of the biasing means, the engaging means making a clicking sound as they engage and disengage and as they reach either limit of rotation and hence axial movement of the hollow rod and the rotating engagement means. |
| F3 | disclosed | claim-6 | the hollow rod is externally threaded to fit into the tapped hole at the cap end of the intermediate tube so that inward axial motion of the hollow rod is stopped at its limit by the cap and outward axial motion of the hollow rod is stopped at its limit by the rotatable engaging member against the cap end of the intermediate tube. |
| F4 | disclosed | claim-2 | biasing means inside the non-rotatable sleeve adapted to seat into at one end the biasing seating means of the annular connector; |
| F5 | disclosed | claim-3 | wherein the engaging members have annular crowns of teeth. |
| F6 | not_found | claim-4 | wherein the annular crowns of teeth of the fixed and rotatable, engaging members are of metal whereby upon engaging and disengaging an audible clicking sound is made. |

### US5944436A

| 要素 | 建议状态 | 原文位置 | 引文 |
|---|---|---|---|
| F1 | not_found | claim-2 | a housing, an ink holder, a fit member, a gear set, and an operative member as components combined together; |
| F2 | not_found | claim-2 | said operative member is pressed down to project said ball point so that a tip is disposed outside of said housing and to rotate said main gear which then rotates said subordinate gear to let said eccentric gear rotate to snap said elastic strip, which then gives out sounds. |
| F3 | not_found | claim-2 | said operative member having a push portion located on an outer surface of said housing, a rack on an inner side, an elongate member extending toward the lower portion of the housing from said rack and contacting and able to push said fit member downward, |
| F4 | not_found | claim-2 | surrounded by a coil spring, said coil spring having one end hooking a projection on said ink holder and the other end hooking a groove of said housing, |
| F5 | not_found | claim-2 | said gear set contained in an upper portion of said housing, consisting of a main gear, a subordinate gear, said main gear consisting of a pinion engaging said rack of said operative member and a large gear, said subordinate gear consisting of a pinion engaging said large gear of said main gear and an eccentric gear engaging said elastic strip; |
| F6 | not_found | claim-2 | said subordinate gear consisting of a pinion engaging said large gear of said main gear and an eccentric gear engaging said elastic strip; |

## 本次观察到的边界

- 改动项 F6：模型将原专利的金属齿冠引文定位到 `claim-4`，对新增的 POM 材料限定给出 `not_found`。这只说明本次文献中的模型建议，不构成全库不存在该方案的证明。
- 修改版 F4：模型引用了较宽的 `biasing means`，该引文本身没有完整点明 compression spring。原文其他位置有弹簧相关描述，专业复核时应补充更直接的引文，不能把“文字存在”当作“语义充分”。
- 在线检索探针确实取得 5 条结果，但包含推进容器、牙刷等候选；这些结果没有全部进入两篇固定文献的对照范围。因此本次展示的是可复现的工程试运行，不是完整现有技术检索。
- 原版和修改版都保持 `reviewed=false`，以验证系统不会把模型自评变成人工确认，也不会直接输出“可授权”。

## 运行检查

- 只改变一个技术要素，其他要素逐字保持不变。

- 所有文献来自真实 Google Patents 页面，保存公开日期、段落与快照哈希。

- 模型标为已披露的引文，必须通过现有代码的逐字回查。

- 三个 skill 的教学基础 eval 已重跑，见下表；它不代表本次真实专利判断正确率。

| Skill | 基础 eval |
|---|---|
| langgraph | 20/20 |
| llamaindex | 10/10 |
| deepeval | 4/4 |

## 复核与复现

- 由专业人员确认逐项披露状态、同义表述、同一技术方案及充分公开，再提交正式复核。

- 对修改点补查非专利文献、更多专利与相关日期；未发现不等于不存在。

- random seed 仅复现修改点选择，模型回答可能波动；本次原始对照和运行用量保存在 JSON。

- API Key 只在内存中使用，不写入文件、输出、Git 配置或历史。