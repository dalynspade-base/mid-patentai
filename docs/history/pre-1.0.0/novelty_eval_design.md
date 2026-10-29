> 当前1.0.0架构与语义eval见[六个业务skill与独立评估](semantic-skills.md)。下文记录此前版本设计及工程兼容基线；数值指标不作为当前语义验收。

# 新颖性预筛与基础 eval 设计

本轮范围由用户指定：CNIPA 新颖性，资料仅 Google Patents；按用户确认的整套技术要素比对，暂不区分独立/从属权利要求，也不自动展开引用关系。

## 规则依据与输出

- [CNIPA《专利法》第二十二条](https://www.cnipa.gov.cn/art/2020/11/23/art_97_155167.html)：新颖性与创造性、实用性分开。未发现否定新颖性的证据，不能推出可授权。
- [CNIPA 中日人工智能专利审查案例对比研究报告，新颖性部分](https://www.cnipa.gov.cn/module/download/downfile.jsp?classid=0&filename=c7bc94c506844c54918b0e08dfbc5708.pdf&showname=report.pdf)：逐项技术方案与每项现有技术分别比较，不能拼接不同文献或互斥技术方案来直接否定新颖性。

程序只输出以下预筛标签：

| 标签 | 含义 | 条件 |
|---|---|---|
| potential_novelty_risk | 发现单篇文献完整披露风险 | 基准日前公开、原文完整、全部要素有逐字引文、同一技术方案、逐项复核记录直接且无歧义的技术含义与充分公开；0.3.0 网页使用模型复核，明确非人工确认 |
| no_complete_disclosure_found | 本次已复核文献中未发现完整披露 | 当前全部文献均完整并逐项复核，无未知或获取错误；每篇各有未找到要素 |
| insufficient_evidence | 证据不足 | 缺日期、缺正文、未知要素、检索失败、未复核或可能涉及抵触申请等 |
| out_of_scope | 超出范围 | FTO、侵权或其他任务；不联网，返回范围提示 |

日期使用严格的基准日前公开条件。公开日在基准日当日或之后时不自动作为普通现有技术；可能涉及中国在先申请、日期链不完整的文献保留人工核验提示。当前不自动判断优先权有效性、抵触申请成立条件、新颖性宽限期、申请历史或特殊披露情形。

## 三个 skill 的独立验收

| Skill | 实现 | 基础 eval |
|---|---|---|
| patent-langgraph | skills/langgraph_skill.py，实际 StateGraph | 逐文献规则、失败停止、日期、引文、越界、模型建议不能自行变成复核结论；独立模型复核接口严格校验并保留模型来源 |
| patent-llamaindex | skills/llamaindex_skill.py，实际 VectorStoreIndex | 中英同义词召回相同原文；保留否定、数值、关系；未知词不报 NaN；不改写原始证据 |
| patent-deepeval | skills/deepeval_skill.py，实际 BaseMetric/LLMTestCase | 裁判接收正确答案，拒绝错误答案、没有来源的风险标签和损坏输出 |

## 用户要求的误判边界如何保留

| 原表情形 | 基础用例 |
|---|---|
| D1=A+B，D2=C，不能拼接否定 A+B+C 新颖性 | do_not_combine_documents |
| 材料中只写 A+B，C 未知 | unknown_is_not_absent；legacy_fto_unknown_feature 另查越界 |
| 已确认产品缺 C 且无等同 | legacy_fto_missing_feature 保留事实，期待 out_of_scope，不新增侵权判断 |
| 开放式权利要求 A+B+C，产品增加 D | legacy_fto_added_feature 检查越界；new_extra_feature_not_ignored 检查新颖性要素不被忽略 |
| 同义词、英文、大小写和语序 | synonym_cases；reviewed_chinese_synonym_same_result；reviewed_english_synonym_same_result |
| 检索失败、空结果、不完整正文 | search_failure_not_safe；empty_search_not_safe；snippet_not_full_document |

补充互斥实施例、否定句、错误引文、公开日缺失、同日公开、后公开及潜在抵触申请。

## 同义词处理的边界

`config/synonyms.json` 是人工维护的领域术语表。界面允许补充组别，同组术语统一用于检索，但返回原始证据。它是可解释的检索基线，不能解决所有自然语言同义问题。

1. 压力传感器 / 压力感测器 / pressure sensor 等变体应召回相同来源。
2. 温度传感器与压力传感器，串联与并联，10 kPa 与 100 kPa，含有与不含有必须保持区别。
3. 一个词出现在否定句中也可能被召回，所以自动定位结果始终是 unknown；人工或模型提出披露候选后，仍需复核真实技术含义及所有限定。
4. 未收录术语不能据低相似度断言不存在；可扩充词表或使用可选模型对照。术语表随报告保存以便复跑。

## 扩展前后的成对检索评测

0.4.0 将检索相关性Eval与新颖性规则Eval分开。运行 `tools/run_synonym_eval.py` 比较原词、词典、原始LLM候选和过滤后扩展；相同题目、K、排序器与最大预算下，按理论/工程分别报告Recall@K、Precision@K、逐题退化及成本。缺缓存/失败/未标注不计为负例，真实参考需独立人工来源及固定池绑定。默认4道合成题包含改善和变差，不能证明真实召回提升。详见[同义词扩展](synonym-expansion.md)。

## 排序与新增相关文献的独立检查

0.5.0 用 `tools/run_synonym_eval.py --compare-rankers --k 3` 固定查询、返回池、参考和成本，仅切换旧词汇排序与新规则排序。每个方案分别报告Recall@K、Precision@K、排序变化、新增相关/不相关公开号及新增相关@K；未知不能算负例。原扩展变差题不改标签，旧排序退化仍保留为回归对照。

规则排序测试覆盖名词堆积与真实关系、背景中实际披露、索引技术自身、关系反向、否定、SI等值换算及单位大小写；复杂范围留给正文判断。模拟模型测试检查逐字引用、全部要素覆盖、输入/输出绑定、失败回退及未读候选保留。端到端界面测试检查追加正文、只复核新增篇目和保留原有效风险。以上是工程契约检查，不能衡量模型真实语义准确率。

在线报告的“扩展新增正文相关”是有效模型/历史人工复核的观察结果，不能作为该模型的独立金标准；标题摘要判断与正文判断分列。基线原词查询不完整时，只能报告暂定扩展独有结果。真实检索评估仍需独立专业参考和专利族隔离，详见[候选排序与补取](candidate-ranking.md)。

## 运行与解释结果

```powershell
$runId = Get-Date -Format 'yyyyMMdd-HHmmss-ffffff'
.\.venv-novelty\Scripts\python.exe -X utf8 -m skills.deepeval_skill --output ".local/verification/basic-$runId.json"
.\.venv-novelty\Scripts\python.exe -X utf8 tools/run_verification.py --output-dir ".local/verification/engineering-$runId"
```

基础 eval 无需 API Key，禁止把教学合成公开号当真实检索结果。所有教学文献标记 synthetic_eval；正常 Harness 拒绝将其用于真实预筛。原表的 FTO 例子作为越界回归保留，而不是按 FTO 规则打分。

当前报告的通过率只反映固定合成题，不证明真实检索覆盖率、同义词覆盖率或专业法律判断准确率。下一阶段应建立真实 Google Patents 快照题，由专业人员独立标注，按专利族分离开发集与盲测集；每条记录基准日、要素、证据、判断和分歧。金标准不输入被测检索器，真实结果需同时报告风险漏报、错误放行、拒答及覆盖率。

旧 modules/eval_ranker.py 仍可评测固定候选集排序，不能解释为 Google Patents 全库覆盖率。

0.2.0新增[真实来源运行与参考接口](real-evaluation.md)，与合成题分开。真实输出先保存、参考后读取；尚无独立人工参考的两套方案保留证据不足，漏报/误放不填零。原方案与POM变更属于同一案例族，不是独立盲测集；现有教学参考也不是外部独立的专业标注。
