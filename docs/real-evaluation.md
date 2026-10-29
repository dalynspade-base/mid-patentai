# 真实来源与真实语义评估

真实来源运行、离线工程契约、真实模型语义评估分别记录。取得 Google Patents 页面证明来源可追溯；工程断言证明约定程序行为；二者都不能直接证明模型语义正确。

当前评估使用实际业务导出的 `skill_records`。先冻结实际输出，再读取独立参考；记录参考来源、作者、依据和绑定。参考可缺失，但必须明确缺失，不能用待测输出自行生成专业金标准。专业语义能力还需要独立参考和隔离案例。

```powershell
python tools/run_semantic_eval.py --records 实际证据.json --references 独立参考.json --output-dir 新预览目录
```

默认只生成外发预览。实际模型验收添加 `--run-model --confirm-external`，按六维分别给出可接受、需修订或待裁决；业务缺步和裁判失败单列，不形成完整验收结论。细节见[公共规范](semantic-skills.md)。

公开页面连通性检查使用 `tools/smoke_google.py`，每次使用新目录。历史来源/规则回归使用 `tools/legacy/run_real_patent_eval.py --legacy-engineering`，必需参数和边界见[历史工程说明](../engineering/legacy/README.md)。不将其数值统计混入当前语义验收。

[整理前真实运行与参考协议](history/pre-1.0.0/real-evaluation.md)。既有来源快照与报告不重写。
