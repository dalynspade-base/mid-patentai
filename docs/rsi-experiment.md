# 术语扩展 RSI

> 2.6.0：当前智慧芽编译与候选归因见[智慧芽协议](patsnap-search.md)，测试隔离及RSI闭环见[测试协议](testing.md)。历史模式按其实际来源保留。

小 RSI 比较同一套公开副本在不同术语生成指令下的表现。正式术语扩展 Skill 是基线。每轮把一条已封存的候选规则加入临时文件，生成审核后的中英文术语及查询变体。程序向智慧芽发送短词组合，保留实际查询、顺序、来源、公开日和原文。逐篇对照与权项汇总各自使用正式指令。

## 信息边界和采纳

- 提议先于所有试跑。提议者只收到正式术语扩展规则、合成开发例和此前未评分的候选文字。实验不把副本、参考名单、实际结果、分数或采纳反馈加入提议请求或临时 Skill。
- 执行者收到公开副本。候选术语须通过现有规则，并由独立调用确认未收录的新词在当前要素中等义。候选仅用于本轮查询。检索端收到实际检索式。
- 先冻结预测，再由本机独立评分模型复核。程序沿用 v1.2 的检索35、逐篇证据45、权项结论20。缺复核、未判和名单外文献按现有手册协议留档；名单外文献需独立补参考方可采纳。
- 两版都可采纳，且总分严格提高、各分项不退步，才保留临时增补。候选在同一开发集上选择。提供锚集时，只在选完后比较初始和最终版本，使用不同专利家族的独立案件。
- 配对试跑的智慧芽平台、查询预算、正文预算、模型和评分协议相同。服务暂停或获取失败时保留失败记录，当前未完成试跑不计分。逐篇对照或权项汇总达到输出上限时，以同一完整输入重试一次，最高 12,000 token。

## 执行

先用现有副本 A 的派生材料检查协议和预算：

```powershell
.\.venv-novelty\Scripts\python.exe -B -X utf8 tools/run_rsi.py --input history/local/datasets/copy-a-v12/public/input.json --gold history/local/datasets/copy-a-v12/private/gold.json --rounds 1 --max-queries 8 --max-documents 25
```

教学流程使用合成响应：

```powershell
.\.venv-novelty\Scripts\python.exe -B -X utf8 tools/run_rsi.py --demo --rounds 1 --output-dir .local/rsi/term-demo-new
```

真实运行沿用已配置的执行模型与智慧芽授权。另在本机启动独立的 OpenAI 兼容评分服务，设置 `RSI_RUBRIC_BASE_URL`（如 `http://127.0.0.1:8081/v1`）、`RSI_RUBRIC_MODEL`，按服务需要设置 `RSI_RUBRIC_API_KEY`。评分服务须在本机执行并保管数据。程序仅允许回环地址，评分请求禁用代理和重定向。也可以对冻结预测准备人工复核包，再用 `tools/score_rsi_results.py` 离线算分。

在预检命令末尾加 `--run-model --confirm-external --output-dir .local/rsi/term-live-new` 启动真实试跑。每案每轮分别测试保留版和候选版，另跑一次基线。每条智慧芽查询最多返回 25 篇，正文默认最多尝试 25 篇，检索及正文调用使用平台额度。

每次运行保存 `config.json`、`sealed_proposals.json`、各轮临时 `SKILL.md`、冻结预测、独立复核、评分和采纳记录。完成时生成 `result.json` 与 `result.md`。详细评分与参考只用于评审和审计。

[双材料评分](dual-material-scoring.md) · [智慧芽接入说明](patsnap-search.md)
