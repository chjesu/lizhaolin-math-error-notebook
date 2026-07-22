# 唯一主库说明

本项目唯一活动题库是：

`data/math_notebook.db`

所有导入、检索、错题记录、推荐、复习和统计操作都必须通过项目级 Skill 的 `scripts/notebook.py` 访问该文件。不要扫描或选择其他 Codex 任务目录中的同名数据库，也不要用外部数据库覆盖本文件。

## 智能体交接

1. 先读取项目根目录 `AGENTS.md` 和 `.agents/skills/math-error-notebook/SKILL.md`。
2. 运行 `python -B .agents\skills\math-error-notebook\scripts\notebook.py bank-info --json`，确认输出路径指向本项目的 `data/math_notebook.db`，且 `integrity_check` 为 `ok`、`foreign_key_violations` 为 `0`。
3. 导入外部记录时忽略记录自身的 `verified` 值；先以未验证状态入库。
4. 逐题核对题干、选项、答案、解析、知识点、来源和重复项后，使用 `annotate --verify` 提升状态。
5. 禁止以抽样、来源名或直接 SQL 批量将题目设为已验证。

## DeepSeek 副本处置

2026-07-18 的 Codex++/DeepSeek 会话在另一任务目录创建了本库副本，并导入 57 题、批量翻转验证状态。该副本不是主库；其操作已在 `data/audits/2026-07-18-deepseek-operation-record.md` 和 `data/audits/2026-07-18-deepseek-db-diff.json` 中留档。审核后只保留有独立证据支持的更正，外部活动副本应移入回收站。
