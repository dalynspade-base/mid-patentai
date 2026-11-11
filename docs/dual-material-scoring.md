# 两套测试材料的评分与运行

> 2.6.0：当前智慧芽编译与候选归因见[智慧芽协议](patsnap-search.md)，测试隔离及RSI闭环见[测试协议](testing.md)。历史模式按其实际来源保留。

2.2.0 使用材料自己的评分协议。执行入口仍为本地真实检索 RSI；当前保留三种可识别协议。

|材料|自动选择的协议|评分|
|---|---|---|
|#8公开训练材料|evidence-v2|实际检索比例与逐文献/逐目标证据分，默认各50%；候选相关性F1不冒充检索召回|
|归档副本A、等级标准v1.2|manual-v1.2|检索35、逐篇证据45、两条总体结论20，保留原始手册公式|
|旧2.1输入|legacy-label-v1|兼容原检索比例与整案等级一致分|

不同协议分开运行、分开报告，不能把总分平均或相互比较。原始附件、原始标准和#8候选/锚池材料均不改写。

## 生成两套独立输入

用户材料：

```powershell
# 仅PDF文本提取需要可选依赖；已经生成的JSON运行时不需要该依赖
python -m pip install -r requirements-data.txt
python -X utf8 tools/prepare_rsi_dataset.py --archive history/local/testsets/copy-A-squeezed-light-cn/v1.2 --output-dir .local/datasets/copy-a-v12-new
```

也可通过 `--input-text` 提供从同一公开副本提取的文本。适配器提取两条完整权项并记录PDF和派生文本的哈希；不会把缺失Markdown的旧哈希冒充PDF哈希。
现有v1.2的重合点、差异、原文定位、逐权项标签可直接供评分端使用，不要求补成#8的 feature_status/evidence_sections 格式。

#8材料：

```powershell
python -X utf8 tools/prepare_rsi_dataset.py --pr8-dir evals/testsets --output-dir .local/datasets/pr8-train-new
```

只把原 train 材料转换为运行参考，原 anchor 保持留档，不自动形成锚案。16篇train材料中5篇满足本地基准日前相关目标口径。
晚公开B版本不凭同族日期得检索信用，必须实际取得显式较早A文本。编号权利要求/摘要位置映射到本地解析器位置；不猜说明书任意段号。
原始26篇候选与train/anchor划分仍原样保存在 `evals/testsets/`。

每套派生目录包含 `public/input.json`、`private/gold.json`、`provenance.json`。执行者仅取得public输入；private标准只给评分端。生成过程不检索、不调用模型。

## 先预览，再显式运行

```powershell
python -X utf8 tools/run_rsi.py --input history/local/datasets/copy-a-v12/public/input.json --gold history/local/datasets/copy-a-v12/private/gold.json --max-documents 25
python -X utf8 tools/run_rsi.py --input history/local/datasets/pr8-train/public/input.json --gold history/local/datasets/pr8-train/private/gold.json
```

预览会显示实际评分协议、分项权重、请求预算和私有评分参考是否参与外发。正式运行需另加新的 `--output-dir`、`--run-model --confirm-external`。
默认正文预算仍20，v1.2完整名单25篇，需显式提高预算；失败和非目标也占尝试预算。
已取消本地单篇/单次输入的字符上限，执行、复核、汇总及改进都传递完整输入；预览和配置记录 `max_input_characters: null`。模型服务自身的上下文窗口仍有效，服务拒绝时保留失败记录，不截断必要限定或补造评分。

## v1.2 原公式

- 检索分：`35×(0.65×加权Recall@25 + 0.35×NDCG@10)`。权重R3/R2/R1为3/2/1；NDCG增益为 `2^等级-1`。保留实际返回顺序，重复公开号只占第一次位置。
- 逐篇分：具体重合、原文定位、关键差异各1；两条标签各0.5，标签正确且有相符证据才得分。`证据分=11.25×Σ(逐篇分×文献权重)/Σ参考权重`，满分45。未检索到的参考保持在完整分母内，文献分为0。
- 总体结论：权1、权2各10分；结果、证据与解释同时相符才通过。权1高风险与权2部分覆盖的逐篇结论分别记录，不合成一个整案等级。
- 总分：三项相加，最高100。报告另列原本A是否找回、误报/漏报、P/C混淆、未判及判断覆盖率；误报率分母为实际已判的非H文献×权项，完整时为49。

## 含义评分与留档

检索、排序、标签匹配、引文逐字回查、哈希和加权运算由代码完成。具体重合是否准确、差异是否实质、标签是否有相符证据以及总体解释是否成立，不能靠字符串相等证明。

因此先保存执行者的 `predictions.json`，再单独请求评分复核。复核器取得原标准、实际原文和冻结输出，只返回布尔判定及书面依据；代码计算分数。它沿用同一已配置模型、独立请求，不冒称另一模型或专家确认，没有加入多模型比较。
私有标准只进入独立本机评分服务，执行、检索和提议请求不含私有参考。真实运行需显式确认公开副本和查询的外发范围；预览不调用接口。

每次试跑保存独立的 `rubric-review/*.prompt.json`、`*.raw.txt` 和 `rubric_reviews.json`。复核绑定公开输入、原标准及预测哈希，旧复核不能用于新输出。缺复核时保留检索分，证据/总体/总分为null，不能采纳。未判标签按手册不获标签分，同时阻止自动采纳。
返回的名单外文献先列为待补参考，不擅自贴无关标签，也不自动采纳。没有提供的同族身份映射不自动推断，其他公开版本须先核验映射。

已有人为独立复核包时可离线评分，不再调用模型：

```powershell
python -X utf8 tools/score_rsi_results.py --input <公开input.json> --gold <私有gold.json> --predictions <冻结predictions.json> --reviews <独立rubric_reviews.json> --output-dir <新结果目录>
```

#8不需要 `--reviews`；已有逐要素标准由纯评分器对账。手工复核必须明确标记来源，不能提交被测模型自报的数字作为独立评分。

## 采纳与锚集

仍逐轮重新测试保留版和候选版；总分严格提高且**每个分项**均不退步才采纳。v1.2分别保护检索35、证据45、总体20三个分项，不能用结论涨分掩盖证据退步。
独立锚案仍在选优结束后才读取，且使用相同评分协议。未提供独立锚案的运行可完成，但只能报告当前材料上的结果。25篇参考仍属于一个案件。

这些分数是当前标准下的对账结果。构造响应测试通过不证明真实模型的专业准确率或全库召回。
