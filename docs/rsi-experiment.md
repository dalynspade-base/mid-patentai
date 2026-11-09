# 临时 Skill 的小型 RSI 实验

本模块按“提议 P → 对照试跑与固定评分 F → 采纳 R”运行，默认五轮，另有一次基线。优化对象只有临时 Skill 的补充文字；模型权重、正式 Skill、工具代码、测试标签和评分规则不进入自动修改范围。本地资料库已可归档用户数据；是否可直接运行须通过协议检查，不能将文献清单视为已核验的逐要素金标准。

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

## 固定评分与采纳（兼容协议）

每例检索分 = `100 × 去重后匹配的应找回目标数 / 应找回目标总数`。分母是完整名单；重复检索、重复公开版本和名单外文献均不增加分数。是否找回原专利 A 另存，不把只找到 A 当成完整召回。

兼容协议 `legacy-label-v1` 的风险分 = 与预置等级完全一致得 `100`，否则 `0`。没有擅自假设等级间距离，也不由被测模型给自己分级质量分。默认总分 = `检索分 × 0.5 + 风险分 × 0.5`；有多个副本时，两个分项分别按 case 平均后加权。权重可在运行前通过参数设置，运行中固定。

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

## 2.2.0：evidence-v2 证据评分

RSI 已取消本地单篇/单次序列化输入的字符上限；执行、独立评分复核、汇总及改进均传递完整输入。配置与预览中的 `max_input_characters` 为 `null`，表示未设置本地字符上限。模型服务自身的 token 窗口、输出预算及调用限制仍有效；超出服务能力时记录真实失败，不截断正文继续评分。

检索继续使用本地 `ModelPatentTrial`，每轮重新规划查询、取回正文并判断，不将 #8 的候选包塞给检索者。
正式八份 Skill、六维语义文字验收和业务界面不变；#8 的 `rsi_loop.py`、多模型盲测说明与专用测试不作为本版入口。

公开 `cases[]` 可增加 `assessment_targets`：每项含 `id`、`title`、`features`，每个要素含 `id`、`text`。
对应私有 `gold.cases[]` 增加 `assessment={"profile":"evidence-v2","references":[…]}`；每项 reference 含
`document_id`、`target_id`、`label`；label 含 `relevance`、`prior_art_at_cutoff`、`coverage`、`feature_status`、`evidence_sections`。
每篇参考须覆盖全部目标，disclosed 须有独立核对的原文位置；不从被测模型的输出反造参考。来源/同族不同版本须分别取回及核验。
旧 `risk_label` 字段为兼容保留，evidence-v2 的采纳分不使用这个整案值；各权项由独立的 evidence_judgments 评分。
示例见 `examples/rsi/evidence-v2.input.json` 与 `evidence-v2.gold.json`，均为明确合成教学数据。

- **检索分**：实际取得的应找回目标数 / 完整目标数 ×100；同族按显式 aliases 去重。另列 P/R/F1 与待标注文献；它们是本参考集合对账，不是全库召回。
- **证据分（risk_score 字段）**：对实际取得且有私有参考的文献，按目标分别计算 `100×(0.5×候选相关性F1 + 0.5×逐目标判断正确率)`，再按目标/案件平均。日期、全部必要要素及允许证据位置均正确才给完整判断信用。
- **总分**：仍按冻结的两项权重，默认检索/证据各50%。不把两个权项合并成一个风险标签；与旧协议及其他手册的分数不直接比较。
- **未判**：返回文献的判断缺失、重复、伪造引文、快照绑定错误时记录未判；证据分与总分为 null，不用零分代替。名单外文献留待独立补标，当前不得采纳。
- **漏检**：没有取回某个参考目标，计检索漏检，不伪造一次文献判断。成功检索却没有正文目标时检索/证据均零信用；接口失败仍停止实验。
- **采纳/锚集**：两版判断都有效后，仍要求每轮新配对、总分严格提高、检索/证据分均不退步；独立锚案仅在选优结束后读取。#8 的 train/anchor 只作材料元数据，不自动进入本地训练或锚集。

真实 evidence-v2 每次试跑最多 `1 + 文档预算×目标权项数` 次模型请求，另有每轮一次改进请求；默认五轮含11次试跑。CLI 预览按目标数计算预算。
`--max-documents` 默认仍为20，允许预先明确设置1—100；失败/未来文献也占获取尝试预算。公开日严格早于基准日才计入新协议。

## Google Patents 旧入口

在本地 `.env` 中配置 `GOOGLE_PATENTS_ENTRY_URL=https://www.google.com/patents/` 后，
检索先将完整查询发给该入口，核验它返回的官方跳转，再通过 `patents.google.com/xhr/query` 取得结果。
单篇先访问 `www.google.com/patents/公开号`，核对跳转仍是同一公开号，再按原请求语言取正文。
这条旧入口仍依赖 `patents.google.com`，不属于独立镜像，不能保证解决该站的 503。
新运行的 `config.json` 在 `runtime.search_entry` 中记录选定入口；网络观察日志分别记录实际请求。
规范来源 URL、原材料、历史快照及其哈希规则保持原样；不接受其他域名或不同公开号的跳转。
未配置该变量时沿用直接访问 `patents.google.com` 的方式。

## 免费检索的连通检查与重试

本地默认配置改为 `GOOGLE_PATENTS_ENTRY_URL=https://patents.google.com/`，直接请求免费的 Google Patents 检索接口和正文。
`www.google.cn/patents` 在2026-11-05的主页、尾斜杠和带查询地址实测均为404，不作为备用接口。
公网服务无法承诺永久不返回503；程序针对429、500、502、503、504及连接/读取超时最多发起3次相同请求，默认等待5、15秒。自动查询拦截页立即暂停，不继续重试；重试用尽暂停5分钟。
遵守不超过30秒的 `Retry-After`；服务要求等待更久时终止本次试跑。404、证书错误和异常跳转直接失败。
重试用尽仍停止，不把网络故障当作成功检索的零命中；不改写查询、不截断正文，也不自动切换到付费来源。
HTTP尝试数与逻辑查询/文档预算分别理解，新运行在 `runtime.http_retry_policy` 中记录策略。真实请求共用5秒间隔队列，已校验响应缓存及未完成计划的恢复见[连接保护与恢复](google-patents-recovery.md)。

下面的检查仅请求公开检索及正文，不调用模型或收费检索；结果保存到新目录，包含每次HTTP状态及取得的原文快照：

```powershell
python -B -X utf8 tools/check_patent_connection.py --query "squeezed light rubidium" --output-dir .local/diagnostics/free-patent-check-new
```

可重复传入 `--query` 检查多组词，或用 `--patent-url` 指定正文。未指定单篇时取第一条查询实际返回的第一篇，空结果视为未验证正文。

## 用户归档材料的兼容检查

```powershell
python -X utf8 tools/check_rsi_archive.py --archive .local/testsets/copy-A-squeezed-light-cn/v1.2 --output-dir .local/evaluations/copy-a-v12-compatibility-new
```

检查只读取档案、核对哈希和接口，不改原件、运行检索或调用模型。结果另存档案之外。
该 v1.2 现在由 manual-v1.2 协议直接支持原35/45/20手册，适配器从副本生成公开的完整权项，无需补成#8逐要素金标准。
材料的25篇是同一副本的参考文献，不是25个独立锚案。提供 PDF 不等于提供标准中引用的 Markdown；两种文件哈希分开记录。

两套材料自动适配、原公式、独立评分复核和运行方法见[双材料评分](dual-material-scoring.md)。
