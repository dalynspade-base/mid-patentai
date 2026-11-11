# 真实来源与独立语义评估

智慧芽取得原文、离线工程回归和真实语义裁判分别记录。先冻结实际业务输出，后读取可选独立参考；记录参考来源、依据和绑定。独立裁判只评实际输出，不从待测模型自报答案构造参考。

使用 `tools/run_retrieval_probe.py` 单独留档智慧芽实际检索；`tools/run_fixed_corpus.py` 隔离逐篇证据判断；`tools/run_semantic_eval.py` 基于真实六步记录生成六维裁决。默认入口只生成预览。运行方式、外发范围与数据边界见[测试协议](testing.md)。

保留历史来源原样。旧 Google 快照与数值回归见[历史工程说明](../history/engineering/legacy/README.md)，不当作新版线上检索或独立语义验收。
