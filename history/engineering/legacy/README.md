# 历史工程回归

本目录保留 1.0.0 之前的来源、规则、固定候选池与数值计算实现。它们不参与当前业务语义验收，不能作为真实模型专业准确率证明。

| 模块 | 保留用途 |
|---|---|
| basic_contracts.py | 历史三框架合成规则回归及 DeepEval 契约适配 |
| synonym_eval.py | 固定候选池的历史排序和扩展指标 |
| real_patent_eval.py | 公开来源获取、快照完整性和历史参考协议 |

夹具位于 `evals/legacy/`，历史测试位于 `tests/legacy/`。需要 DeepEval 的测试在未安装可选依赖时明确跳过；安装 `requirements-engineering.txt` 后可执行完整历史组。

在项目根目录使用当前解释器运行：

```powershell
python -m unittest discover -s tests -v
python tools/legacy/run_real_patent_eval.py --legacy-engineering --source-dir 原文目录 --output-dir 新运行目录
python tools/legacy/run_engineering_trial.py --source-dir 原文目录 --output-dir 新运行目录
```

第二条命令默认读取已有来源包，增加 `--fetch-sources` 才获取公开页面。第三条是显式的历史模型试验，会提示模型密钥并调用模型；不在普通回归中自动运行。输出目录必须是新目录。

原数值排序 CLI 已撤销；函数只由历史测试调用。当前语义评估使用 `tools/run_semantic_eval.py`，离线契约使用 `tools/check_semantic_contract.py`。
