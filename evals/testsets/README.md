# #8 公开评测材料（2.2.0 保留范围）

来源：PR #8，源提交 `91ba465f79422bee965568872a74343d8c3a8aa7`；已合并至 main `ac498c051b0fbc5255cd619a9f78446407b99352`。

- `theory_cases.json`：19个六维语义案例；`engineering_cases.json`：18个规则/契约案例。
- `patents.json`：公开专利语料，含26篇 RSI 候选证据包。
- `rsi_case.json`：公开副本、私有标签与原 train/anchor 元数据；完整文件只供评分端。
- `policy.py` / `score.py`：理论/工程离线对账；`rsi_scoring.py`：上游证据校验与纯判分函数。
- `UPSTREAM.json`：来源及保留文件哈希。以上数据按上游原字节保留。

用户提供的另一套材料保存在被忽略的 `.local/testsets/`，没有复制到本目录或提交。
#8 的训练/锚标签仅留档，不自动形成本地锚集。多模型盲测工作区、固定候选执行循环、上游采纳选项不纳入本版。

当前执行入口是 `tools/run_rsi.py`：实际检索、逐篇/逐目标判断、新配对采纳、选优后独立锚案终验。
相关性 F1 衡量候选分类，实际检索 P/R/F1 单列；构造输出与离线对账不证明真实模型能力。

理论/工程对账：`python -X utf8 evals/testsets/score.py`。
详情见 [本地 RSI](../../docs/rsi-experiment.md) 与 [2.2.0 说明](../../docs/releases/v2.2.0.md)。
