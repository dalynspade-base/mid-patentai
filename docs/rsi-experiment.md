# 临时 Skill 的小型 RSI 实验

本模块按“提议 P → 对照试跑与固定评分 F → 采纳 R”运行，默认五轮，另有一次基线。优化对象只有临时 Skill 的补充文字；模型权重、正式 Skill、工具代码、测试标签和评分规则不进入自动修改范围。当前先提供架构，没有真实修改副本和约二十篇标注名单。

参考 [《递归之桥》](https://share.onorca.dev/a/akZtQQ86ZK-C) 的 P/F/R、固定评价依据和独立锚集思想。本实现限定为文档自我修订，不声称通过少量训练轮次证明普遍能力提升。

## 分工与数据界限

|部分|实现|可取得的信息|
|---|---|---|
|公开输入|`input.json`|修改副本全文、判断基准日、可用风险等级|
|私有参考|`gold.json`|全部应找回专利、原专利 A 身份、正确风险标签；仅评分器读取|
|执行者|`modules/rsi_trial.py::ModelPatentTrial`|当前临时 Skill、公开输入、同一查询/读取预算；真实查询与逐篇原文；先逐篇判断再汇总|
|改进者|`ModelSkillProposer`|保留版文字、实际执行记录、聚合分数/错项、上次候选反馈；不取得私有参考表|
|评分器|`modules/rsi_experiment.py::score_predictions`|冻结的预测及私有参考，固定公式，不让模型自报得分|
|控制器|`run_experiment`|新建文档、配对试跑、采纳或拒绝、指纹检查及结果保存|

这是现有 harness 旁的可选实验链路，不改变正式六维语义验收协议。自动补充写到新的 `.local/rsi/…/SKILL.md`，不注册到 `.agents/skills/`。执行者和改进者的模型接口只有文本输入/输出，没有文件编辑、代码执行或任意工具调用权限。程序拒绝输出目录与正式 Skill 目录重叠，并检查正式文件及运行配置的指纹。

## 测试集准备

填写 [`input.example.json`](../examples/rsi/input.example.json) 与 [`gold.example.json`](../examples/rsi/gold.example.json)，另存到 `.local/rsi-data/`。模板标记 `template_unfilled` 会拒绝运行；完整填写后将公开输入 `provenance` 改为 `provided_dataset`。副本先转为实际文本，不自动假定 PDF/附件已解析。

- 一份修改副本是一个 `case`；约二十篇名单是该 case 的全部应找回目标，不是二十个独立风险样本。
- 原专利 A 必须在 `targets` 内，由私有 `source_target_id` 指向；公开输入不携带它的身份、名单或正确等级。
- `cutoff` 是本轮历史判断基准日。目标的已知公开日期不能晚于它；实际检索只将已取得正文、具有基准日前公开日期的文献计入。
- 每个目标建议给出公开号；同一家族/其他公开版本需要显式放入该目标的 `publication_ids`。有公开号时按编号匹配，不因标题相同把不同文献混在一起。
- 名称列表尚无公开号时可留空编号，按规范化后的标题或明确的 `title_aliases` 精确匹配。不会使用模型自由判定的近似标题匹配；翻译和别名需要显式补齐。
- 风险等级沿用已有标注。等级列表和“资料不足”标签运行前固定；若使用数字等级，在 JSON 中写成字符串。来源、标注理由和日期应在实际数据整理时人工核对。

## 固定评分与采纳

每例检索分 = `100 × 去重后匹配的应找回目标数 / 应找回目标总数`。分母是完整名单；重复检索、重复公开版本和名单外文献均不增加分数。是否找回原专利 A 另存，不把只找到 A 当成完整召回。

风险分 = 与预置等级完全一致得 `100`，否则 `0`。没有擅自假设等级间距离，也不由被测模型给自己分级质量分。默认总分 = `检索分 × 0.5 + 风险分 × 0.5`；有多个副本时，两个分项分别按 case 平均后加权。权重可在运行前通过参数设置，运行中固定。

每轮重新运行当前保留版和候选版，采用同一模型配置与每例预算。默认每次试跑最多四条查询、二十次去重 URL 正文获取尝试，失败/未来文献也消耗预算。总分严格提高且两个分项均不退步才保留；打平或分项退步均拒绝。单次配对不是统计显著性证明。

运行配置、输入/参考摘要、正式文档及评分代码指纹留存。预测先保存，再评分。模型查询超预算、引文伪造、接口失败或正文取回失败会停止并记录失败，不用零分或补造分数代替有效试跑。正文超过载荷上限整体拒绝，不截断限定继续评分。

## 五轮中的文档与反馈

```text
.local/rsi/新运行目录/
  config.json                         固定预算、权重、数据/文档/评分器指纹
  baseline/patent-rsi-trial/SKILL.md    正式指令的临时起点副本
  baseline/trial/predictions.json      基线实际输出
  baseline/score.json
  round-01/ … round-05/
    patent-rsi-trial/SKILL.md          不变基线 + 已采纳补充 + 本次提议 + 先前反馈
    proposal.json                     本次补充及理由
    control/predictions.json          当前保留版重新试跑
    candidate/predictions.json        候选版重新试跑
    evaluation.json                   两版分数及采纳决定
    feedback.json                     给下一轮的聚合反馈
  result.json / result.md             简短汇总、各轮分数、采纳及最终保留版路径
```

每轮 `SKILL.md` 写入后保持不变，确保确实测试的是该份文字。本轮得分写入旁边的 `evaluation.json`，再载入下一轮临时 Skill；不在评完后改写已经验收的文档。被拒绝的补充仍留档，但不进入下一轮保留版。失败时生成 `failure.json`、`failure.md`，不生成完整提升结论。

自动流程不会将最佳临时版覆盖为正式 Skill。提议只能是有长度限制的增补段落；含代码块、额外编辑字段或私有参考身份的提议被拒绝。这些约束不能证明文字没有语义层面的投机，因此还需要检查候选文字及独立数据结果。

## 使用

先看五轮教学演示，不联网、不调用模型，分数来自预设响应：

```powershell
.\.venv-novelty\Scripts\python.exe -X utf8 tools/run_rsi.py --demo --rounds 5 --output-dir .local/rsi/demo-新编号
```

真实数据准备后，先验证并查看预算预览，默认不执行模型：

```powershell
.\.venv-novelty\Scripts\python.exe -X utf8 tools/run_rsi.py --input .local/rsi-data/input.json --gold .local/rsi-data/gold.json
```

显式运行时，使用项目已配置的 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`；每轮执行者与改进者分开请求，但可使用同一模型，不冒称独立专家。私有参考文件不发送给它们。真实运行会发送修改副本、临时指令和取得的专利原文，查询会发送到 Google Patents：

```powershell
.\.venv-novelty\Scripts\python.exe -X utf8 tools/run_rsi.py --input .local/rsi-data/input.json --gold .local/rsi-data/gold.json --rounds 5 --output-dir .local/rsi/live-新编号 --run-model --confirm-external
```

单例五轮含基线及成对试跑，共十一轮试跑；每轮一次查询规划、最多二十次逐篇判断、一次汇总，最多二百四十二次执行者请求和五次改进请求（不含接口层重试；文献不足或只有一篇时更少）。每个试跑都重新调用模型，不回放旧预测。没有跨轮搜索缓存，联网结果可能变化，真实比较仍可能受模型和网络噪声影响。

## 锚集与结果解释

可增加 `--anchor-input … --anchor-gold …`。锚集在全部采纳完成后才读取，分别测试基线和最终保留版，不参与提议或选优。重命名同一份输入不算独立锚集；不同副本仍需按原始专利/家族安排隔离，代码不能自动证明划分独立。

未提供锚集时，报告标记 `not_measured_no_anchor`。在同一个修改副本上反复反馈，可能学会固定输出某个等级，不能仅凭分数上涨声称真实的新颖性判断能力提升。即便约二十篇全部找回，仍需要核查全文披露、限定、日期及标注是否可靠。
