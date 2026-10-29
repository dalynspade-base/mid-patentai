# 材料接手与案件留存

本扩展基于 `dalynspade-base/mid-patentai` 的 `18d473932f2e6cc13db945bd0ccaaab04c444de8`；材料接手部分参考 `LeoFitz2000/patent-precheck` 的 `60868823e2b56e6663820f707e7597cce75017e1`。保留原有流程、来源说明、报告与评测记录。

## 已接入的最小流程

在原有网页中描述想法或上传 PDF、DOCX、TXT、MD、PNG/JPEG，保存为 `.local/cases/case-.../`。最多 8 份、合计 10 MB，原字节与 SHA-256 留存；网页保存不调用模型，不表示附件已经解析。

保存材料时，已有的发明描述、要素和当前案件保持不变；空白页面才自动载入新案。需要切换时选择“已保存的案件”并打开，替换前的文字草稿及最近已生成报告会保存至 `.local/drafts/draft-.../`（设置 `PATENT_CASES_DIR` 后，位于该目录旁的 `drafts/`）。只保存工作内容，不保存模型密钥或完整会话状态。草稿文件中的报告是最近一次生成的版本，可能与后来编辑的文字不同。

在本项目对应的 AI 工作区说“开始初筛”，Agent 按技能读取实际材料，调用 `modules.material_cases.save_prepared_features(root, case_id, data)` 保存带出处和未知项的要素。每轮在案件 `preparation-history/revision-.../` 新建文件，成功事件记录路径和哈希；网页载入最新已成功记录的版本，兼容读取旧 `prepared_features.json`，不覆盖旧文件。返回网页点击“载入 AI 整理的要素”，接入现有检索、快照、对照和判断流程。载入要素不覆盖已有发明描述，替换已有要素前会留存草稿。

AI 客户端需要具备读取文件的工具；附件读取或解析失败应记录缺口。保存及载入时会核对案件清单和原始附件哈希，拒绝用户描述、UTF-8（含BOM）或带BOM的UTF-16 TXT/MD中的假引文。PDF、DOCX、图片及无法可靠解码的文字附件明确记录“未机器校验”，不将上传成功或原文包含某句话当作语义确认。

材料未知项和未机器校验警告进入规则，阻止无缺口的“本次未发现完整披露”；已有完整披露风险仍保留并附警告。改动要素不清除原未知项，旧整理出处也不自动成为新要素的证据。报告记录实际载入的整理版本、哈希及引文校验状态。

0.3.0 将网页人工逐项复核改为大模型逐篇逐项复核，明确记录模型来源；材料缺口约束仍保留。配置模型并确认外发后调用，参见[证据复核说明](evidence-review.md)。网页自动提取附件及自动完成整个链路仍未实现，保存材料本身不调用模型。用户仍可手填要素。

报告可下载，也可点击“保存本轮结果到案件”，每次在 `results/run-.../` 新建 Markdown/JSON，追加报告保存事件。原始清单与附件不改，旧报告不覆盖。真实案件、依赖环境及测试材料由 Git 忽略。

## 启动和验证

按 README 安装 requirements.txt 到 `.venv-novelty` 后，在项目目录运行 `powershell -ExecutionPolicy Bypass -File start_novelty.ps1`，或 `.venv-novelty/Scripts/python.exe -m streamlit run app.py --server.address 127.0.0.1`。依赖环境不随 Git 同步，每台设备需分别安装。

当前工程和语义契约使用 `python tools/run_verification.py --current-only --output-dir 新运行目录`；完整运行去掉 `--current-only`，历史工程单列。离线契约使用 `python tools/check_semantic_contract.py`。真实语义评估使用 `tools/run_semantic_eval.py`，只有明确外发确认后才调用裁判。完整安装与启动步骤见 [README](../README.md)。

早期验证次数、浏览器记录与人工复核阶段说明保留在[整理前快照](history/pre-1.0.0/local-integration.md)，不作为当前语义验收结果。

## 来源与后续协作

`material_cases.py` 改编自 patent-precheck 的材料大小/后缀约束、附件命名清理、原字节哈希、不可覆盖案件和独立结果思路；`material_ui.py` 将入口接入现有 Streamlit；新增 Skill 改编其自动接手、原文核查、预算和未知管理规则。仅引入可复用代码与规则，不包含个人记录、真实案件、密钥、旧工程或历史实验包。

团队成员通过同一个主仓库协作，分别拉取代码并安装环境。`.local/` 与测试构造材料被忽略，不通过普通 Git 提交同步。
