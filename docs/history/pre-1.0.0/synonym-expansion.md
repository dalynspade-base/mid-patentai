> 当前1.0.0架构与语义eval见[六个业务skill与独立评估](semantic-skills.md)。下文记录此前版本设计及工程兼容基线；数值指标不作为当前语义验收。

# 同义词四层扩展与检索评测

0.4.0 在 Google Patents 查询前增加词典、模型候选、审核和成对评测。术语扩展只服务查询召回，不把LLM新词写入证据嵌入词典或法律等同判断。此前的大模型逐项复核继续有效；这里的新增术语审核只决定哪些查询允许发送。

```mermaid
flowchart TD
    F[用户确认的完整技术要素] --> T{选择检索策略}
    T --> THEORY[理论类：保护专名、固定译名与缩写]
    T --> ENGINEER[工程类：部件、结构、工艺、材料和功能表达]
    THEORY --> DICT[第一层：预定义词典]
    ENGINEER --> DICT
    DICT --> LLM[第二层：确认外发后由LLM生成中英术语和常见缩写]
    DICT --> RULE[第三层：来源、词边界、语义关系与限定检查]
    LLM --> RULE
    RULE -->|词典已确认且限定保留| ACCEPT[允许查询]
    RULE -->|词典外的新词| PENDING[待审核：逐项说明来源和技术含义]
    PENDING -->|明确选择并确认| ACCEPT
    RULE -->|上位/下位/相关概念、限定变化或保护术语| REJECT[拒绝，不发送]
    ACCEPT --> QUERY[原要素优先，按类型有界生成替换和组合查询]
    QUERY --> GP[Google Patents：保留实际词、候选、失败和成本]
    QUERY -.-> EVAL[第四层：冻结结果与独立相关性标签的成对Eval]
    EVAL --> METRIC[比较Recall@K、Precision@K、逐题退化与检索成本]
    METRIC --> DECIDE[依据真实评估决定扩展策略，不能以词多为优]
```

## 理论类与工程类的不同

它们是按当前内容选择的查询策略，不是互斥的法律分类；混合技术可分别运行比较。系统不凭标题自动断言专利属于哪一类。

| 项目 | 理论类 | 工程类 |
|---|---|---|
| 侧重点 | 算法/数学方法/标准等专名、固定译名、规范缩写 | 部件、结构名称、连接表达、工艺、材料及功能表述 |
| 新LLM候选类别 | `term`，不把专名泛化为功能或部件 | `term/component/structure/process/material/function` |
| 每个原术语的词典别名 | 最多1个 | 最多2个 |
| 每个原术语的已审核LLM别名 | 最多1个 | 最多2个 |
| 每个要素的查询变体 | 最多2个 | 最多8个 |
| 组合 | 单术语替换，不做组合 | 单术语替换，再组合两个不重叠的术语位置 |
| 总查询预算 | 8条 | 16条 |
| LLM候选总量 | 最多12条 | 最多36条 |

先安排所有原要素查询，再按要素轮流安排变体。原句保留否定、连接方向、数值、单位和顺序，不先归一化整句再改写。每条最多400字符，超长或预算外项明确记录，不截断。正文获取预算仍独立于查询预算。自定义检索词保持用户指定范围，不自动附加逐要素或LLM词。

## 词典与LLM候选的边界

第一层沿用 `config/synonyms.json` 和用户补充词组，按完整术语定位后替换，英文不匹配另一个单词内部。大小写可用于识别术语，但原查询及mW/MW等单位不改写。词典自身也需领域维护，并接受检索Eval，不保证每次扩展都有效。

第二层调用 `generate_llm_candidates`。发送全部确认要素、检索类型、保护术语和词典，不发送专利原文或API Key；密钥仅用于请求头。载荷超过2万字符时拒绝发送。候选含 `feature_id/source/term/language/category/relation/rationale`，`source`必须是原要素中的完整逐字术语。英文缩写要有明确技术含义，不能自行发明。

第三层拒绝未知/不完整来源、查询操作符、保护术语改写、数值/单位/否定/方向/连接变化，以及明确标为 `broader/narrower/related/unknown` 的语义关系。词典已收录且检查通过者为 `accepted`；未收录的新词为 `pending`，规则不能独立证明任意新词的语义等价。页面选中候选并确认当前技术含义后才允许查询。硬性拒绝项不能靠选择绕过。更改要素、词典、类型、保护词或模型会重新建立候选与确认；重生成改变候选或选择时确认失效。

不要把“更宽泛可能召回更多”作为同义词接纳理由。工程类部件别名多，因此增加表达与有界组合，同时保留精度和成本检查；不放宽含义一致性的要求。只需词典时可以直接检索，不调用模型，也不强迫审核新词。

## Eval设计与指标

四个方案共享同一题目、K、文献池、排序器和该类型的最大查询预算：

- `original`：只发原要素。
- `dictionary`：原要素加预定义词典。
- `llm_raw`：原要素优先，再使用未经语义审核的候选，余量补词典；仅在离线评测中用于诊断漂移，绝不作为正式检索的绕过入口。
- `llm_filtered`：词典加规则/明确审核通过的LLM术语。

各方案的实际查询数可以不同，分别记录成本。使用正式查询规划器；缓存结果按公开号去重，并使用正式检索相同的部件/关系/提及语境规则排序（0.5.0），保留原词汇分数，不把相关性参考输入查询或排序器。

`Recall@K = 前K条中的相关文献数 / 固定已标注池内的相关文献总数`；`Precision@K = 前K条中的相关文献数 / K`。不足K条时精度分母仍为K；相关文献总数为0时Recall不可计算。缺缓存、缓存失败、参考不完整或TopK有未标注项时，质量指标不可计算，不当成负例或零分。固定池有任何未标注文献均视为参考不完整；零相关题的Recall不可计算，但Precision仍参与配对。配对宏平均按各指标可计算的相同题目分别统计分母，理论/工程分别汇总，同时列出每一道Precision变差的题，不能用平均值掩盖退化。

成本保留查询数、实际缓存HTTP次数、独立候选数、LLM逻辑调用数/HTTP重试数、输入/输出字符、服务返回的token用量与耗时。没有提供的实际用量标为未记录，不猜测货币费用。候选生成结果被会话复用时不重新调用模型，搜索记录保留其生成成本作为来源，不冒称每次重搜都重新计费。

```powershell
.\.venv-novelty\Scripts\python.exe -X utf8 tools/run_synonym_eval.py --k 3 --compare-rankers
```

默认新建 `.local/evaluations/synonym-时间戳/`，输出 `evaluation.json` 与 `evaluation.md`，不覆盖历史。加 `--compare-rankers` 同时输出 `ranking_comparison.json/md`：每组的查询列表、返回池与成本完全相同，仅比较旧词汇排序和新规则排序。逐组列出新增相关/不相关公开号和TopK中的新增相关数，相关性只读取独立参考，不输入排序器。缺原词基线或有效参考时增量不可计算。网页离线示例也输出这份对比。网页“运行同义词扩展示例 Eval（离线）”使用同一实现。

默认数据 `evals/synonym_retrieval_cases.json` 是4道作者编写的合成固定池，包含新部件名改善召回、扩展增加噪声、保护算法专名及已知缩写。查询缓存、候选和参考均显式保存，可检查；模拟候选并非真实LLM输出，0次模型调用只指这次离线示例。它验证流程，不能证明真实Google Patents检索改善。原“扩展增加噪声”案例和参考标签原样保留：旧排序仍会退化，新排序恢复TopK，但该题新增相关文献为0；这是排序修复，不能称为新增相关召回。在线增量的元数据/正文分层判断见[候选排序说明](candidate-ranking.md)。

## 接入真实固定池

复制示例结构，改为 `provenance=google_patents_frozen_pool`、`reference_author_type=human`；每个案例提供：

- 原始要素、检索类型、专利族 `family_id`、保护词、冻结词典和独立保留的模型候选/审核记录。
- `documents`：完整固定候选池的公开号、真实Google Patents单篇URL、标题和摘要；可附原始快照哈希。
- `results_by_query`：四组计划中每条实际查询的 `status`、最多5个返回公开号和已记录的 `http_requests`；实际空结果须显式写 `ids=[]`，未运行的查询不能填空冒充已运行。
- 由独立专业标注者给出的 `judged_ids`、`relevant_ids`、`reference_complete`。真实参考记录 `reference` 中含 `author_type=human`、`reviewer`、带时区的 `reviewed_at`、`basis`，以及 `modules.synonym_eval.relevance_input_hash(case)` 对题目/完整池计算的 `input_hash`。自填身份不等于系统核验资质。
- `generation_cost` 填入实际LLM生成记录；未知字段留空/不填，不能补造零调用或token用量。

然后使用 `--dataset 路径 --k 10 --output-dir 新目录`。候选生成和参考标注分离，固定词典及提示词后再评盲测；按专利族分离开发/测试集。比较原词→扩展，也比较词典→LLM过滤，检查逐题及理论/工程分组的Recall增益、Precision退化和成本增量，再决定是否扩大词量。缓存池Recall仍不等于Google全库Recall；本模块不会凭合成示例自动声称改善。

## 代码与回归

- `modules/synonym_expansion.py`：策略、词典候选、LLM生成、规则和组合。
- `modules/synonym_ui.py`：外发确认、候选审核、类型选择与评测入口。
- `modules/novelty_search.py`：有界查询、实际检索与成本记录。
- `modules/synonym_eval.py`、`tools/run_synonym_eval.py`：成对指标、固定池与报告。
- `tests/test_synonym_expansion.py`、`tests/test_synonym_ui.py`：模拟模型、漂移、单位、词边界、审核失效与评测契约；不代表模型专业准确率。
