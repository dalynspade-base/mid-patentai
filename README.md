# Google Patents 专利新颖性预筛

**版本：1.1.0** · [本次功能与兼容说明](docs/releases/v1.1.0.md) · [1.0.0架构迁移](docs/releases/v1.0.0.md) · [变更记录](CHANGELOG.md)

本项目把用户想法或已保存材料整理为技术要素，检索或接收 Google Patents 原文快照，逐篇对照技术关系与全部限定，再给出有证据范围的新颖性预筛报告。1.0.0 将流程组织为“一个主 skill、六个业务子 skill、一个独立语义评估 skill”。

当前适用于 CN 新颖性预筛，支持整套确认要素，或按明确版本和引用路径展开独立、从属权利要求，分别对照。报告用于定位风险和补充取证，不等于可授权性、创造性、侵权或 FTO 结论。

## 1.1.0：上下文、权利要求与衔接

六个业务skill共用基准日、CN/Google Patents范围、当前限定及目标版本，按各自职责取得必要原文；独立eval按含义激活功能、关系和表达等必查维度。全部步骤完成后另核验跨skill的限定、未知和结论传递，不新增业务skill。

界面可勾选“按独立／从属权利要求及修改版本比对”，导入[教学记录示例](examples/claim_history.json)，选择版本和引用路径。父项限定继承，多项引用分支分别比对；修改前后、来源及依据进入报告，改版后旧复核失效。批量接口为 `NoveltyHarness.run_claims`，详细输入见[上下文与版本协议](docs/skill-contexts.md)。普通要素输入继续可用。

同类案例整理为[申请争议底线](docs/application-baselines.md)，按具体争议原因区分新颖性、创造性、公开充分性、支持和程序，不建专门争议案件库，也不把未授权状态当作新颖性否定。

## 1.0.0 相对上一版本改变了什么

对比基准是已合并的中期提交 `1d1bc25`：其提交标题为“v0.3.0中期汇报”，但当时 `VERSION` 的实际值为 **0.6.0**。本次是从该代码版本升级到 1.0.0，改变了技能发现名称、部分 Python 导入路径及评估接口。

| 项目 | 上一版本 0.6.0 | 1.0.0 |
|---|---|---|
| 技能组织 | LangGraph、LlamaIndex、DeepEval 等框架名称 | 主入口、六个业务子技能、独立语义评估，共八个入口 |
| 评估对象 | 合成规则、固定候选池与数值工程指标 | 各业务技能实际冻结的输入、输出与原始依据 |
| 质量结论 | 数值指标与工程断言分别解释 | 六维文字裁决、逐字引文、反例及修订动作 |
| 状态区分 | 旧评测报告及执行记录 | 业务结论、执行状态、语义验收结论分别记录 |
| 报告生成 | 网页、报告模块和工作流共同补记录 | 报告交付技能统一组装并绑定最终正文 |
| 底层实现 | 框架实现位于 `skills/` | LangGraph/LlamaIndex 移入 `modules/`，仍用于编排和阅读线索 |
| 历史工程代码 | 与当前模块、测试和夹具混放 | 迁至各自的 `legacy/` 区域，数值结果不参与当前语义验收 |
| 依赖 | 包含 Plotly 和必装 DeepEval | 移除旧景观图依赖，DeepEval 改为可选历史工程依赖 |
| 业务范围 | 仍留存旧相似度、改进建议及申请初稿代码 | 移除已脱离主流程的旧业务链，聚焦新颖性预筛 |

沿用的能力包括材料留存、术语扩展、候选相关性、逐篇模型复核以及来源、日期、同一方案和充分公开等预筛约束。这些已有能力不作为本次新引入功能重复计算。

## 安装依赖

已验证环境为 Windows、Python **3.12.14**。建议使用 Python 3.12；其他 Python 版本未在本次发布中验证。

在项目根目录的 PowerShell 中执行：

```powershell
py -3.12 -m venv .venv-novelty
.\.venv-novelty\Scripts\python.exe -m pip install -r requirements.txt
.\start_novelty.ps1
```

若终端限制执行 PowerShell 脚本，可直接启动：

```powershell
.\.venv-novelty\Scripts\python.exe -X utf8 -m streamlit run app.py --server.address 127.0.0.1
```

已有可用环境只需更新依赖，不必删除环境。`requirements-novelty.txt` 是 `requirements.txt` 的兼容安装别名。需要完整历史 DeepEval 回归时，另行安装：

```powershell
.\.venv-novelty\Scripts\python.exe -m pip install -r requirements-engineering.txt
```

## 模型配置与材料使用

网页可直接输入模型配置，也会读取项目根目录的 `.env`。首次使用可复制模板：

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
```

仅在 `.env` 尚不存在时复制；已有配置直接编辑。配置项如下：

| 变量 | 含义 |
|---|---|
| `LLM_API_KEY` | 用户自己的模型密钥 |
| `LLM_BASE_URL` | 兼容 Chat Completions 的服务基址；默认 `https://api.deepseek.com`，不要重复添加 `/chat/completions` |
| `LLM_MODEL` | 服务支持的模型名称；模板值可按实际服务调整 |

保存材料不调用模型。在线 Google Patents 检索需要网络；模型术语生成、逐篇对照和独立语义裁判会单独请求模型，需先确认外发范围。独立评估沿用当前模型配置，指职责和调用阶段分离，不声称使用另一模型或独立专家。

网页支持保存附件，但保存成功不等于已经提取或理解附件。对话材料整理应读取实际原文；无法读取的附件保留未知，不能补造技术结构或效果。真实案件、原始附件、密钥及本地运行结果不提交到 Git。

## 完成一次预筛

1. 输入发明描述或保存附件；也可在本项目对话中使用 `$patent-precheck` 接手已保存案件。
2. 整理并确认技术要素、材料出处、未知项和判断基准日。对话整理完成后，网页可载入 AI 整理的要素。
3. 按技术语境审核扩展词，执行 Google Patents 检索或录入来源明确的原文快照；失败和未读范围保留记录。
4. 预览并确认模型外发内容，逐篇对照全部要素、原文、同一方案、充分公开及上下文。阅读线索不自动成为披露判断。
5. 查看预筛结论、证据位置和未知项，下载 Markdown / JSON；有关联案件时可保存本轮结果。
6. 在报告下方展开“独立语义评估”，核对待发送的技能记录，另行确认后评估已完成技能。

当前网页仍有材料确认、术语审核和模型外发等交互；尚未实现“上传任意附件后自动完成全部专业判断”。

## 八个技能及其职责

| 入口 | 主要输入 | 主要输出 |
|---|---|---|
| [patent-precheck](.agents/skills/patent-precheck/SKILL.md) | 当前材料、任务和执行进度 | 调度六个业务阶段；公开接口 `skills.precheck_skill.NoveltyHarness` |
| [patent-material-preparation](.agents/skills/patent-material-preparation/SKILL.md) | 实际材料原文或确认要素 | 带出处的要素、未知项和整理记录 |
| [patent-synonym-expansion](.agents/skills/patent-synonym-expansion/SKILL.md) | 要素、语境、词典与保护术语 | 候选关系、审核状态和保留限定的查询变体 |
| [patent-candidate-search](.agents/skills/patent-candidate-search/SKILL.md) | 查询计划、基准日或提供的快照 | 实际查询、候选文献、来源和检索缺口 |
| [patent-evidence-alignment](.agents/skills/patent-evidence-alignment/SKILL.md) | 技术要素、选定原文及阅读线索 | 逐项支持/缺口/未知、原文位置和同一方案依据 |
| [patent-novelty-assessment](.agents/skills/patent-novelty-assessment/SKILL.md) | 有效复核和来源/日期等约束 | 有范围边界的新颖性预筛结论 |
| [patent-report-delivery](.agents/skills/patent-report-delivery/SKILL.md) | 实际执行结果、案件和证据 | 统一组装并绑定的 Markdown、JSON 与技能记录 |
| [patent-semantic-eval](.agents/skills/patent-semantic-eval/SKILL.md) | 冻结的技能输入/输出、原文与可选参考 | 六维发现、语义裁决、引文、反例及后续动作 |

主入口负责调度，独立裁判分别检查六个业务技能的实际输出。尚未完成的技能不作为已通过验收的技能。

## 六维语义评估及结果解释

| 维度 | 检查重点 |
|---|---|
| 术语与对象 | 技术语境中的等义、上下位、相关概念及指称对象 |
| 结构与关系 | 连接、控制方向、施受角色、输入输出和组合 |
| 逻辑与限定 | 条件、否定、范围、顺序、可选项、技术数值和单位 |
| 语境与指称 | 指代、同一方案、背景、引述、假设与实际披露 |
| 功能与效果 | 功能限定与实现手段；相同目的不能补足结构缺口 |
| 评价与表达 | 评价措辞是否改变、掩盖或扩大技术含义和结论 |

六维是本项目的任务检查维度，不声称语言学文献规定了固定六层理论。各技能均返回六维意见；必要维度不能跳过，其他维度不适用时须解释原因。维度不加权、不累加，不输出分数、概率、通过率或高中低评级。

| 结果类别 | 可能结果 | 含义 |
|---|---|---|
| 业务预筛结论 | 单篇完整披露风险／本次已复核文献未发现完整披露／证据不足／超出范围 | 回答本次资料范围内的业务问题 |
| 技能执行状态 | `completed` / `partial` / `not_executed` / `failed` | 说明业务步骤是否实际完成 |
| 语义验收裁决 | 可接受 `acceptable`／需修订 `needs_revision`／待裁决 `pending_adjudication` | 说明实际输出是否忠于依据和任务义务 |
| 整体评估执行 | `completed` / `incomplete` | 缺少必要技能或裁判失败时，不生成完整验收结论 |

“证据不足”是业务结论，“待裁决”是验收意见，二者不相互替代。裁判返回 `activated_dimensions`，已触发维度不能跳过；确定的维度判断同时引用实际输出和原始依据；违反项提供反例及修订动作。裁判给出“需修订”不会自动改写业务结果。技术数值、日期、哈希以及内部运行成本可以保留，它们不作为语义质量分数。

## 命令行评估与验证

以下命令均在项目根目录执行，输出目录每次须使用新名称。无需激活环境，统一调用同一解释器。

```powershell
# 离线裁判契约自测，不调用模型、不产生业务语义验收
.\.venv-novelty\Scripts\python.exe -X utf8 tools/check_semantic_contract.py
# 工程及历史回归分组运行；历史 DeepEval 项需要可选依赖，否则明确跳过
.\.venv-novelty\Scripts\python.exe -X utf8 tools/run_verification.py --output-dir .local/verification/release-check
# 仅当前业务工程/语义契约；发现测试时也不导入历史可选依赖
.\.venv-novelty\Scripts\python.exe -X utf8 tools/run_verification.py --current-only --output-dir .local/verification/current-check
# 从实际导出 JSON 冻结技能记录，只生成外发预览
.\.venv-novelty\Scripts\python.exe -X utf8 tools/run_semantic_eval.py --records .local/input/novelty_evidence.json --output-dir .local/evaluations/preview
# 单独预览术语扩展；其他技能可通过 --skill 指定
.\.venv-novelty\Scripts\python.exe -X utf8 tools/run_synonym_eval.py --records .local/input/novelty_evidence.json --output-dir .local/evaluations/synonyms
```

先将网页导出的证据 JSON 保存到示例中的输入路径，或替换为自己的实际路径。旧报告没有 `skill_records` 时，需要重新执行业务；不能把历史数值结果直接改名为语义结果。

CLI 读取进程环境变量，**不会自动加载 `.env`**。实际模型评估前，在当前 PowerShell 会话配置 `LLM_API_KEY`、`LLM_BASE_URL` 和 `LLM_MODEL`，核对外发计划，再使用新的目录执行：

```powershell
.\.venv-novelty\Scripts\python.exe -X utf8 tools/run_semantic_eval.py --records .local/input/novelty_evidence.json --output-dir .local/evaluations/judged --run-model --confirm-external
```

可用 `--references` 提供与实际输出绑定的独立参考，或 `--skill` 仅评一个业务技能。缺少参考如实标为 `none`；`--demo` 仅生成教学计划，不能和 `--run-model` 一起使用。预览生成 `frozen_skill_outputs.json`、`semantic_eval_plan.json`；真实调用另生成 `semantic_eval.json` 和 `semantic_eval.md`。详见[结果和参考协议](docs/semantic-skills.md)。

命令执行成功不等于验收可接受：实际运行在 `execution=completed` 时可正常退出，即使 `verdict=needs_revision`；执行不完整返回非零状态。自动化调用方应读取 JSON 中的执行状态和裁决。

公开页面连通性可用 `tools/smoke_google.py` 显式检查。它只检索公开通用词、不发送用户发明，默认写入新的 `.local/verification/google-smoke-*` 目录。

## 目录、升级与数据保留

```text
.agents/skills/       八份技能定义
skills/              八个业务程序入口
modules/             当前业务实现、状态图、阅读线索与语义裁判
engineering/legacy/  历史数值工程模块
tools/               当前验证和语义评估入口；legacy/ 下为历史试验
tests/               当前工程/语义契约；legacy/ 下为历史工程测试
evals/legacy/        历史教学及固定候选池夹具
docs/releases/       版本对比与迁移说明
docs/history/        整理前设计文档快照
reports/             既有历史报告与索引
.local/              本地案件、运行、验证和备份（不提交）
```

从 0.6.0 升级时，应更新旧 Python 导入和评测命令；旧框架命名的 skill 入口已经撤销。LangGraph 和 LlamaIndex 的实现仍保留，DeepEval 只在历史工程中使用。[完整迁移表](docs/releases/v1.0.0.md#迁移与不兼容变更)列出旧、新位置。

原始附件、`case.json`、已有整理版本、原文快照、历史哈希和报告不自动迁移或覆盖。历史报告按原含义读取，不能自动补成新的语义验收记录。真实材料、凭据、依赖环境和新增运行记录保持本地。

## 本次验证与能力边界

1.0.0发布时通过189项当前工程/语义契约、28项历史工程测试；1.1.0验证结果见[本次说明](docs/releases/v1.1.0.md)。八份技能定义继续保留。测试使用构造材料和模拟模型响应；这些次数是工程检查记录，不是语义质量分数或独立专利样本数。

本次未证明真实模型专业语义准确率，也未证明 Google Patents 全库召回改善。真实能力仍需独立参考、隔离案例和有出处的语义复核。未发现完整披露不等于不存在相关现有技术；日期链、抵触申请及充分公开等特殊情况仍可能需要专业核验。

[材料留存](docs/local-integration.md) · [术语扩展](docs/synonym-expansion.md) · [候选检索](docs/candidate-ranking.md) · [证据对照](docs/model-evidence-alignment.md) · [历史工程](engineering/legacy/README.md) · [历史报告](reports/README.md)
