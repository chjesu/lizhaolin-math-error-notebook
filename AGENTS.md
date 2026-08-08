# 李兆霖数学错题本项目规则

- 项目文本编码固定为 UTF-8。Windows PowerShell 读取文本必须显式使用 `Get-Content -Encoding UTF8`，不得先按系统默认编码读取再猜测重试；Python 启动命令使用 `python -X utf8 -B`，文件读写显式指定 `encoding="utf-8"` 或 `utf-8-sig`。禁止用无编码参数的重定向改写项目文本。
- 通过 Codex++、DeepSeek 或其他外部模型运行时，先运行 `notebook.py doctor --json` 和与任务匹配的 `notebook.py agent-context --task <task> --json`；仅在预检失败或输出要求时读取 `DEEPSEEK_STARTUP.md`/专项参考。
- 修改或新建功能前先查 `PROJECT_ARCHITECTURE.md`，优先扩展现有权威入口，禁止创建平行数据库层、推荐器、验证器或通用打印器。
- 错题、题库、推荐和复习任务使用项目级 `math-error-notebook` Skill，并通过其脚本操作。
- 唯一主库为 `data/math_notebook.db`；不得发现、合并、复制覆盖或改用其他同名库。
- 照片批改须保存结构化记录；不清晰项明确说明；数学用 LaTeX；无直接证据不得归因“粗心”。
- 判题输出标准：逐题给出判定。若题目有错误或部分正确，必须展示完整原题、学生作答、第一处实质错误、正确解析过程和最终答案；若存在两种及以上真正不同且适用的解题思路，分别列出。原题、符号或图形不清晰时不得补造，须明确指出并请求清晰局部图。完全正确时给出简洁核验依据即可。
- 推荐仅限已验证题并显示来源/理由；默认生成不附答案的 A4 PDF，不打印。仅在用户明确要求时附答案或调用打印机。
- `2026-07-19-g11-beijing-20` 批次，以及自 `2026-07-20`（含）起入库的高质量试卷题，经用户确认可免除每题的完整独立重解；但仍须逐题核查题干、选项、答案与解析逻辑自洽、标签、来源和重复项，发现疑点时恢复独立推导，并通过 `audit-item → verify-item` 正式记录。用 `audit-summary` 查看可简化数量，用 `audit-queue/prepare-audit-batch --simplified-only` 筛选日期范围。
- 不满足上述条件的题，外部 `verified`、来源信誉、抽样或批量 SQL 均不能替代题干、答案、解析、标签、来源和重复项的逐题独立审核及 `verify-item`。
- 仅导入开放授权或用户确认有权使用的材料；不得绕过登录、付费墙或访问限制。
- 可机械化步骤必须优先调用现有脚本：照片错题先 `photo-preflight`，模型先读 RapidOCR 文本和可选 PaddleOCR 公式候选，只按需查看预览/疑难裁剪，再 `grade-preview → grade-commit`；候选推荐用 `recommend-packet`，审核准备用 `prepare-audit-batch`，模型精简决策用 `prepare-review-batch` 扩展，大量已完成的逐题审核用 `verify-review-batch` 提交，交接用 `handoff`；按日期导入 DOCX 用 `scripts/import_recent_docx_batch.py`，结构预检用 `scripts/audit_recent_docx_batch.py`。OCR 只作辅助，公式候选也不可信任；公式、图形和手写步骤必须视觉复核。不得让模型重复拼装这些固定结构，也不得用批处理放宽逐题验证质量门。
- 为减少 token 消耗，已由模型形式化为 SymPy 表达式的代数、方程、恒等式和代入检查，优先批量调用 `python -B scripts/symbolic_precheck.py <checks.json>`；默认只把 `fail`/`unknown` 项交回模型扩展推理。`pass` 只证明提交的形式化表达式成立，不替代题意、图形、定义域、分类、标签、来源、重复项及 `audit-item → verify-item` 复核。
