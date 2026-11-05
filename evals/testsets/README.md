# 语义 Eval 测试集（理论 + 工程 + RSI）

本目录包含三类互补评测：六维语义理论集、规则/契约工程集，以及带训练池与锚池的 RSI 逐篇判断循环。理论和工程集使用 **10 篇真实公开专利**；RSI v2 使用 **26 篇候选证据包**，其中 6 篇为 2026-11-05 逐字核验后新增的基准日前硬近邻。

## 与 rsi-lab 的对应关系

| rsi-lab | 本目录 |
|---|---|
| `policy.py` 评分细则（代码化真值，被测 Agent 不可见） | `policy.py` 细则条文（R01–R14）+ 各 cases JSON 里的 `label`/`expected` 字段 |
| `data/train.json`（36 份，循环用） | `theory_cases.json` / `engineering_cases.json` 中 `usage: "train"` 的案例 |
| `data/anchor.json`（40 份，从不参与决策，只对账） | 同上 `usage: "anchor"` 的案例（`--split anchor` 查看锚集对账） |
| `audit.py`（用细则答案给每轮候选对账） | `score.py`（对冻结候选输出 / 规则引擎输出对账） |
| `check.py`（环境自检） | `score.py` 工程集部分（离线、零模型、零网络） |

## 文件

| 文件 | 内容 |
|---|---|
| `patents.json` | 理论/工程语料及 RSI 26 篇候选证据包：公开号、标题、日期、逐字摘录、证据范围；原机器翻译痕迹保留，新增近邻保存中文公开文本 |
| `theory_cases.json` | **理论集**：19 案（训练 13 / 锚 6），每案 = 真实原文 + 冻结候选输出 + 隐藏细则答案（六维违反、业务结论、语义裁决） |
| `engineering_cases.json` | **工程集**：18 案（决策 14 / 契约 4），对齐 `modules/novelty_rules.assess_novelty` 与 `modules/novelty_models` 数据契约 |
| `policy.py` | 细则条文（R01 引文逐字 … R14 裁决推导）与空白归一化工具 |
| `score.py` | 理论/工程对账打分器（见下） |
| `rsi_case.json` | RSI 公开副本、训练/锚池分池及评分端答案；不得整体发给被测模型 |
| `rsi_scoring.py` | RSI 证据校验、逐目标判分、独立检索对账与采纳准则 |
| `rsi_loop.py` | RSI 离线 mock、本地/外部模型、手工提示词包和结果导入入口 |
| `BLIND_TEST_WORKSPACE.md` | 可复制到独立空白工作区的单文件多模型盲测说明，内嵌 3 篇公开 case，由当前窗口模型的独立 subagent 逐篇执行 |
| `../../tests/test_rsi_loop.py` | 盲测泄漏、证据、误报/漏报、失败不采纳、手工包和本地端点回归测试 |

## 语料（10 篇，全部逐字核验）

| 公开号 | 公开日 | 主题 | 主要考点 |
|---|---|---|---|
| US4968168A | 1990-11-06 | 咔嗒圆珠笔 | 互斥实施例、优选措辞（preferably metal）、伪造引文 |
| US5944436A | 1999-08-31 | 发声笔 | 否定词（cannot engage）、指称对象（弹性片21 vs U形部41）、功能≠结构 |
| US8053972B2 | 2011-11-08 | 量子点白光LED（MIT） | 光致转换 vs 电致发光、正确锚定用例 |
| CN108559503B | A 2018-09-21 / B 2020-07-07 | 华中科大 Cs2AgBiBr6 制备 | 独权/从权数值界限、工序顺序、同族版本日期链、跨文献拼接 |
| US20170194101A1 | 2017-07-06 | Stanford Cs2AgBiBr6 吸收层 | 记号差异（Cs2AgBiBr6 vs Cs2BiAgBr6）→ 待裁决示范 |
| US7802572B2 | 2010-09-28 | 透明医用口罩 | 摘要 optionally vs 权利要求必要限定、自定义术语边界（transparent 含 translucent） |
| CN115863457A | 2023-03-28 | 云南师大 Cs2AgBiBr6 电池 | 摘要概括词 vs 权利要求逐层限定、数值界限、日期门、CN 抵触申请 |
| CN110408993A | 2019-11-05 | 宁波大 X 射线探测晶体 | 背景技术不是方案披露、用途限定 |
| US20240423306A1 | 2024-12-26 | 铜+银 TENG 抗菌口罩 | 例示性摘要（in some examples）≠ 权利要求限定 |
| US3664335A | 1972-05-23 | 三层外科口罩 | 单条权利要求逐项正确锚定用例 |

## 用法

```powershell
# 全部对账（理论集 + 工程集）
.\.venv-novelty\Scripts\python.exe -X utf8 evals\testsets\score.py
# 只跑某一集 / 只看锚集 / 单查一案 / 保存 JSON
.\.venv-novelty\Scripts\python.exe -X utf8 evals\testsets\score.py --set theory
.\.venv-novelty\Scripts\python.exe -X utf8 evals\testsets\score.py --split anchor
.\.venv-novelty\Scripts\python.exe -X utf8 evals\testsets\score.py --case T05
.\.venv-novelty\Scripts\python.exe -X utf8 evals\testsets\score.py --json .local\evaluations\testset-report.json
```

## 打分口径

**理论集**（候选输出 vs 细则答案）逐案检查：
1. `quote_verbatim`——disclosed 行的引文按空白归一化后能在所引小节逐字找到（R01）；
2. `section_map`——引文小节是否符合细则指定的层级（R08：摘要≠权利要求、独权≠从权）；
3. `statuses`——逐项 disclosed/not_found/unknown 是否与细则一致（R02/R05/R07/R12）；
4. `conclusion`——业务结论四选一是否正确；
5. `verdict`——候选自报裁决 vs 细则裁决（R14：有违反→needs_revision，诚实未定→pending_adjudication）；
6. `required_mentions`——必提事项（如 T15 必须提到同族 A 的公开日 2018-09-21）。

边界如实标注：纯语义问题（如 T06 工序顺序被丢弃）机械对账覆盖不到，输出标"机械未覆盖语义层，按细则裁决"，这类案例是给独立语义裁判 / 人工复核用的，正是本项目 `patent-semantic-eval` 六维裁决的入口。

**工程集**（规则引擎 vs 细则答案）：把构造输入喂给真实 `assess_novelty`，比较返回的四类结论；契约用例直接调用 `google_patent_url`、快照哈希绑定、`parse_features`、快照版本校验。全部离线、确定、可重复。

## label 纪律

- label 由 `policy.py` 的细则从原文事实推导；**被测流程不得读取 label/expected 字段**（与 rsi-lab"Agent 看不到 policy.py"同口径）。
- `usage: anchor` 的案例（理论 T05/T10/T13/T14/T15/T17，工程 E14）**不参与任何迭代调优**，只用于对账。
- 首次对账记录：**E04 的细则答案曾被纠正**——初版基准日取 2023-06-01（在公开日之后），引擎正确判 potential_novelty_risk，label 错；修正基准日为 2022-12-15（落在申请日 2022-11-30 与公开日 2023-03-28 之间）后 18/18 一致。对账机制本身抓住了细则错误，这正是锚集的用途。

## 当前对账结果（2026-11-04，commit e49f9a7）

- 工程集：**18/18 与细则一致**（14 决策 + 4 契约）。
- 理论集：19 案全部按设计对账——16 个 needs_revision 案例中 15 个被机械对账捕获（T19 伪造引文、T02 否定词、T07 可选/必要、T15 日期链等），1 个（T06 工序顺序）如实标注需语义裁判；T13/T17 为正确锚，T14 为待裁决示范。
- 仓库自身校验未受影响：`tools/run_verification.py --current-only` 全部通过（`all_engineering_checks_passed: true`，记录于 `.local/verification/testset-addition-check`）。

## 诚实边界

- 工程集的逐项复核（rows）是**构造输入**，不是真实人工复核；理论集的候选输出是**冻结的模拟业务输出**，有对有错，用于对账训练，不代表任何真实模型的水平。
- 本测试集衡量的是"输出与细则答案的一致性"，**不是**专业准确率、召回率或语义质量分数；与项目 README 的口径一致，不输出数值评分。
- 语料为 Google Patents 页面逐字转录，机器翻译痕迹（如 `Cs (volatile organic Compounds)2AgBiBr6`、`turpentine absolute alcohol`、`tHe`）原样保留，引文核对按空白归一化，不改写任何非空白字符。
- 理论/工程的 10 篇和 RSI 的 26 篇都不足以覆盖全部现有技术空间；扩充语料时按 `patents.json` 现有格式增补并保持逐字核验。

## RSI v2：组员先读

### 这一环节在做什么

RSI 评测要分别回答三件事：

1. **找相关**：候选与副本是否技术上相关；只有另交独立检索公开号列表时，才计算实际检索 P/R/F1。
2. **读准证据**：每个技术要素是 disclosed、not_found 还是 unknown，披露是否有逐字原文。
3. **判对关系**：日期资格和覆盖程度是否正确，不能把局部重合直接说成单篇完整披露。

当前固定候选模式**不测全库检索**。训练池 16 篇用于提词和采纳，锚池 10 篇只对账，不参与提词、采纳或案例挪动。锚池分数也不称为“真实专业准确率”。

**材料分工**：`public_case` 和程序生成的 case 提示词可以给被测模型；本 README、`rsi_case.json` 的答案字段、分池和标准理由只留在评分端。程序用白名单构造提示词，不发送 `derived_from`、调整说明或标签。

### 副本怎么形成（评分端答案）

模拟副本以 CN108559503B 的同族公开文本 CN108559503A 为依据，把方法限定调整为：摩尔比 2:1:2、纯 DMSO、60℃ 溶解约 2 小时、氯苯 1:2、30℃ 挥发扩散。A 文本权利要求 1—9 与总方法/实施例提供这些要素的组合依据；基准日设为 2018-10-08，A 公开日 2018-09-21 早于基准日，B 授权公告日 2020-07-07 晚于基准日。因此该候选是“同族较早公开文本完整披露”，不是拿晚公开的 B 文本倒填现有技术。

### 判断等级

| 维度 | 取值 | 直白含义 |
|---|---|---|
| 技术相关性 | related / unrelated / unknown | 是否属于应纳入对照的技术内容；相关不等于高风险 |
| 日期资格 | true / false | 公开文本是否早于 2018-10-08；晚公开与技术相关性分开记录 |
| 覆盖程度 | full_disclosure | 同一文献证据覆盖全部六个必要要素 |
|  | partial_coverage | 至少一个完整要素披露，但至少一个缺失 |
|  | local_association | 有相关技术内容，但没有一个完整目标要素被披露 |
|  | insufficient_evidence | 证据范围或语义不足，不能硬判 |
|  | not_applicable | 无关文献，不再给新颖性覆盖等级 |

### 26 篇材料清单（评分端）

| 公开号 | 分池 | 类型 | 覆盖答案 | 基准日前 |
|---|---|---|---|---|
| [CN108559503B](https://patents.google.com/patent/CN108559503B/en) | train | 原始完整披露 | 完整披露 | 是（同族 A） |
| [US20170194101A1](https://patents.google.com/patent/US20170194101A1/en) | anchor | 同材料对照 | 局部关联 | 是 |
| [CN110408993A](https://patents.google.com/patent/CN110408993A/en) | anchor | 同材料对照 | 部分覆盖 | 否 |
| [CN111233035A](https://patents.google.com/patent/CN111233035A/en) | train | 同材料对照 | 证据不足 | 否 |
| [CN113753951A](https://patents.google.com/patent/CN113753951A/en) | train | 同材料对照 | 证据不足 | 否 |
| [CN115863457A](https://patents.google.com/patent/CN115863457A/en) | train | 同材料对照 | 部分覆盖 | 否 |
| [CN109874347A](https://patents.google.com/patent/CN109874347A/en) | train | 钙钛矿对照 | 证据不足 | 否 |
| [CN114732921B](https://patents.google.com/patent/CN114732921B/en) | anchor | 无关对照 | 不适用 | 否 |
| [CN105597728A](https://patents.google.com/patent/CN105597728A/en) | anchor | 无关对照 | 不适用 | 是 |
| [US4968168A](https://patents.google.com/patent/US4968168A/en) | train | 无关对照 | 不适用 | 是 |
| [US5944436A](https://patents.google.com/patent/US5944436A/en) | train | 无关对照 | 不适用 | 是 |
| [US3664335A](https://patents.google.com/patent/US3664335A/en) | train | 无关对照 | 不适用 | 是 |
| [US7802572B2](https://patents.google.com/patent/US7802572B2/en) | train | 无关对照 | 不适用 | 是 |
| [US20240423306A1](https://patents.google.com/patent/US20240423306A1/en) | train | 无关对照 | 不适用 | 否 |
| [US20230180864A1](https://patents.google.com/patent/US20230180864A1/en) | anchor | 无关对照 | 不适用 | 否 |
| [US8053972B2](https://patents.google.com/patent/US8053972B2/en) | train | 无关对照 | 不适用 | 是 |
| [US9793505B2](https://patents.google.com/patent/US9793505B2/en) | anchor | 无关对照 | 不适用 | 是 |
| [US20210202869A1](https://patents.google.com/patent/US20210202869A1/en) | anchor | 无关对照 | 不适用 | 否 |
| [US9199842B2](https://patents.google.com/patent/US9199842B2/en) | anchor | 无关对照 | 不适用 | 是 |
| [US10593902B2](https://patents.google.com/patent/US10593902B2/en) | train | 无关对照 | 不适用 | 否 |
| [CN106757342A](https://patents.google.com/patent/CN106757342A/zh) | train | 新增硬近邻 | 部分覆盖 | 是 |
| [CN107325812A](https://patents.google.com/patent/CN107325812A/zh) | anchor | 新增硬近邻 | 部分覆盖 | 是 |
| [CN107325004A](https://patents.google.com/patent/CN107325004A/zh) | train | 新增硬近邻 | 局部关联 | 是 |
| [CN108034989A](https://patents.google.com/patent/CN108034989A/zh) | train | 新增硬近邻 | 局部关联 | 是 |
| [CN107093671A](https://patents.google.com/patent/CN107093671A/zh) | anchor | 新增硬近邻 | 局部关联 | 是 |
| [CN108560056A](https://patents.google.com/patent/CN108560056A/zh) | train | 新增硬近邻 | 部分覆盖 | 是 |

### 六篇新增硬近邻为什么难

| 公开号 | 容易误判的重合点 | 关键缺口 |
|---|---|---|
| CN106757342A | ABX3 钙钛矿、双容器反溶剂扩散与 F5 重合 | B 位仅为 Pb/Ge/Sn，不是 Ag/Bi 双金属；缺少指定第一析晶、离心和 Cs2AgBiBr6 组合 |
| CN107325812A | 权利要求4公开前驱液、内外容器和反溶剂扩散 | 材料为 ABX3 且 B 位为 Pb/Ge/Sn；缺少两步去除偏析晶、离心及指定参数 |
| CN107325004A | 同属钙钛矿晶体制备，并涉及固液分离 | 采用固体反应流体媒介得到粉末，不是 DMSO 前驱液的两步反溶剂扩散结晶 |
| CN108034989A | 甲胺溴铅、反溶剂蒸气及 20～55℃ 生长相近 | 用载气载入反溶剂蒸气，材料、双容器路径、第一析晶和离心组合不同 |
| CN107093671A | 前驱液、DMSO/氯苯选项和抗溶剂结晶重合 | 是基片/模具内高温生长薄膜并旋干退火，不是两步析晶和双容器扩散 |
| CN108560056A | 权利要求1公开小瓶/广口瓶、反溶剂挥发扩散和密封 | 材料为 A2PbBr4，DMF 室温溶解并过滤；没有指定三组分、第一滴加析晶和离心 |

### 模型做得好不好怎么看

- **候选相关性 F1**：只衡量固定池里相关/无关分得对不对，不叫全库检索召回。
- **判断正确率**：相关候选的相关性、日期、覆盖、逐要素状态及披露证据位置全部正确才算对。
- **错误分类**：单列完整披露误报/漏报、部分覆盖与局部关联混淆、日期错误、证据错误和未判。
- **有效判断覆盖率**：未返回、返回无效 JSON、引文伪造或重复结果都算未判；未判不会自动变成“无关/低风险”。覆盖率不足时训练 F 信号为空，本轮不得采纳。
- **训练 F 信号**：有效覆盖率 100% 时，`0.5×候选相关性F1 + 0.5×判断正确率`，仅用于循环采纳；锚池只报告同一口径的对账结果。

例：模型把 CN106757342A 判成完整披露，即使找到了双容器扩散，也仍会把材料、第一析晶、离心等缺失要素误判，记一次“完整披露误报”；若根本没有交回结果，则记“未判”，不记成正确或低风险。

### 运行与留存

```powershell
# 新增回归测试
python -X utf8 -m unittest tests.test_rsi_loop -v

# 离线 mock：只验证提词、判分、采纳与回退机制
python -X utf8 evals/testsets/rsi_loop.py --mock --output-dir .local/rsi/mock-v2

# 本地 OpenAI 兼容端点：先训练池3篇冒烟，再单轮完整基线
$env:LLM_BASE_URL="http://127.0.0.1:8317/v1"; $env:LLM_MODEL="本地模型名"
python -X utf8 evals/testsets/rsi_loop.py --run-local --smoke --output-dir .local/rsi/local-smoke
python -X utf8 evals/testsets/rsi_loop.py --run-local --cycles 1 --output-dir .local/rsi/local-baseline

# 手工新窗口盲测包及结果导入（不再次调用模型）
python -X utf8 evals/testsets/rsi_loop.py --export-manual .local/rsi/manual-smoke --smoke
python -X utf8 evals/testsets/rsi_loop.py --export-manual .local/rsi/manual-full
python -X utf8 evals/testsets/rsi_loop.py --import-responses .local/rsi/manual-full/responses --output-dir .local/rsi/manual-scored
```

### 独立工作区多模型自动盲测

把 `evals/testsets/BLIND_TEST_WORKSPACE.md` 复制为空白工作区的 `README.md`，不需要复制原项目或 cases 目录。每个新窗口选择待测模型，只需要求 Agent“按 README 自动执行，全部结束后报告结果目录”。主窗口负责调度，使用当前窗口模型逐篇启动独立 subagent，自动保存实际提示词、原始返回、可导入响应和运行清单。

不同模型使用各自的 `runs/<run_id>/`，不读取其他模型结果；该文档只含公开 case，不含答案或分池。完成后将整个运行目录交回主仓库，离线导入其 `responses/`：

```powershell
python -X utf8 evals/testsets/rsi_loop.py --import-responses runs/<run_id>/responses --output-dir .local/rsi/manual-scored
```

本地模式只接受 `127.0.0.1`、`localhost` 或 `::1`，本地无密钥服务可用。外部模型仍用 `--run-model --confirm-external`，不得借本地模式转发。CLI 不自动读取 `.env`。

每个运行目录保存 `run_manifest.json`、各轮 `prompts/`、`raw_responses/`、`validated/`、`scores.json` 和 `result.md`；多轮另存临时指令、训练反馈与采纳依据。手工包包含 `START_HERE.md`、统一指令、逐篇 case、响应模板和可选独立检索提示词。运行记录不保存密钥。

2026-11-05 离线验证：15 项新增测试通过；既有工程集 18/18 与细则一致；`tools/run_verification.py --current-only` 共 228 项通过、0 失败、0 跳过，记录位于 `.local/verification/rsi-v2-check-venv`。ineq mock 五轮训练 F 信号为 0.450→0.500→0.778→0.944→1.000；sign 运行时第2、4、5轮拒绝，最佳采纳停在第3轮，验证了小样本限制与回退路径。上述轨迹来自预设剧本，**不是任何真实模型成绩**。真实本地/手工结果只有实际保存原始返回后，才可在对应运行目录中报告。

本机宿主 `PYTHONHOME` 与仓库虚拟环境冲突时，需先去掉该变量再调用 `.venv-novelty`；本次完整回归使用仓库 Python 3.14.2。该处理不改变依赖或系统配置。

## 后续可接

1. 把 `theory_cases.json` 的 `candidate_output` 换成真实 harness 冻结记录（`skill_records`），即可用同一打分器对账真实业务输出。
2. 理论集案例可经 `tools/run_semantic_eval.py --references` 作为独立参考喂给六维语义裁判，验证裁判与本细则的一致性。
3. 新增专利：先在 Google Patents 核验原文，按 `patents.json` 格式录入，再设计案例；细则条文如需扩展，改 `policy.py` 的 RULES 并同步 label。
