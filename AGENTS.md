# 李兆霖数学错题本项目规则

- 通过 Codex++、DeepSeek 或其他外部模型运行时，先运行 `notebook.py doctor --json` 和与任务匹配的 `notebook.py agent-context --task <task> --json`；仅在预检失败或输出要求时读取 `DEEPSEEK_STARTUP.md`/专项参考。
- 修改或新建功能前先查 `PROJECT_ARCHITECTURE.md`，优先扩展现有权威入口，禁止创建平行数据库层、推荐器、验证器或通用打印器。
- 错题、题库、推荐和复习任务使用项目级 `math-error-notebook` Skill，并通过其脚本操作。
- 唯一主库为 `data/math_notebook.db`；不得发现、合并、复制覆盖或改用其他同名库。
- 照片批改须保存结构化记录；不清晰项明确说明；数学用 LaTeX；无直接证据不得归因“粗心”。
- 推荐仅限已验证题并显示来源/理由；默认生成不附答案的 A4 PDF，不打印。仅在用户明确要求时附答案或调用打印机。
- `2026-07-19-g11-beijing-20` 批次经用户确认来源十分可靠，可免除每题的完整独立推演核算；但仍须逐题核查题干、选项、答案与解析逻辑自洽、标签、来源和重复项，并通过 `audit-item → verify-item` 正式记录。该豁免不得扩展到其他来源。
- 除上述指定批次外，外部 `verified`、来源信誉、抽样或批量 SQL 均不能替代题干、答案、解析、标签、来源和重复项的逐题审核及 `verify-item`。
- 仅导入开放授权或用户确认有权使用的材料；不得绕过登录、付费墙或访问限制。
- 可机械化步骤必须优先调用现有脚本：错题先 `grade-preview` 再 `grade-commit`，候选推荐用 `recommend-packet`，审核准备用 `prepare-audit-batch`，大量已完成的逐题审核用 `verify-review-batch` 提交，交接用 `handoff`；按日期导入 DOCX 用 `scripts/import_recent_docx_batch.py`，结构预检用 `scripts/audit_recent_docx_batch.py`。不得让模型重复拼装这些固定结构，也不得用批处理放宽逐题验证质量门。
