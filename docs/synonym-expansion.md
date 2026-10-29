# 术语扩展与语义验收

`skills.synonym_expansion_skill` 调用现有扩展实现，区分原词、词典候选、模型候选和显式审核后的查询变体。理论/工程检索预算、专名保护、否定、关系方向及技术数值的约束保留；增加查询词不代表已经证明语义等价。

扩展记录保留原始要素、领域词典、查询策略、保护术语、每个候选的关系和处理依据。模型生成与审核分开，未经审核的新词不能自动进入查询。

独立语义裁判逐一检查原词与扩展在技术语境中的对象、上下位关系、连接关系和限定是否保持，解释新增词的检索用途及可能偏移。相同目的、相邻技术概念和字面相似不能自行认定为同义。

六维全部给出满足、违反、未确定或有理由不适用；结果为可接受、需修订、待裁决。必要维度、引文和输出协议见[公共规范](semantic-skills.md)。

```powershell
python tools/run_synonym_eval.py --records 实际导出记录.json --output-dir 新预览目录
```

默认只生成预览；实际调用裁判需添加 `--run-model --confirm-external`。旧 `--k`、`--compare-rankers` 等参数已经撤销。固定池数值对照留在 `engineering/legacy/synonym_eval.py` 和 `tests/legacy/`，不作为当前验收。

[整理前方案及历史数值结果](history/pre-1.0.0/synonym-expansion.md)。
