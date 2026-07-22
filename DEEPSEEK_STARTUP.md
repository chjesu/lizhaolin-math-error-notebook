# 李兆霖数学错题本：DeepSeek / Codex++ 快速启动

本文件只用于无法直接判断启动方式时。项目的实时状态、任务规则和下一步命令由权威脚本生成，避免每个智能体重复读取长交接材料。

## 1. 固定入口

- 正式项目名称：`李兆霖数学错题本`
- 项目根目录：`C:\Users\Administrator\Documents\Codex\2026-07-17\new-chat-5\math-error-notebook`
- 唯一主库：`data/math_notebook.db`
- 唯一业务入口：`.agents/skills/math-error-notebook/scripts/notebook.py`
- PDF/打印入口：`.agents/skills/math-error-notebook/scripts/practice_sheet.py`

不得扫描、复制、合并、覆盖或改用其他同名数据库，不得直接 SQL 修改验证状态。

## 2. 每次启动只运行两条命令

```powershell
python -B .agents\skills\math-error-notebook\scripts\notebook.py doctor --json
python -B .agents\skills\math-error-notebook\scripts\notebook.py agent-context --task <任务> --json
```

`<任务>` 只能是：

- `grade`：照片/文字判题与错因记录
- `recommend`：同类题推荐
- `verify`：未验证题审核
- `import`：授权试卷导入
- `review`：复习与作答记录
- `pdf`：练习卷生成/打印
- `maintenance`：题库维护与交接

`doctor.status` 必须为 `ok`。`agent-context` 会返回当前题库数量、关键质量门、实际命令及本任务是否需要额外参考文件。不要为了“了解项目”递归读取题库、`data/audits/` 或 `data/imports/`。

## 3. 代码修改与专项任务

- 修改或新增功能前读取 `PROJECT_ARCHITECTURE.md`，只扩展现有权威入口。
- 导入、来源修复和验证任务按 `agent-context` 要求读取 `references/import-and-verification.md`。
- 错因无法确定时才读取 `references/error-taxonomy.md`。
- 模板校验失败时才读取 `references/data-contract.md`。

## 4. 固定程序化流程

```text
判题：grade-preview → grade-commit
推荐：recommend-packet → 模型复核相关性 → assign-recommendations → practice_sheet.py
批量 DOCX：import_recent_docx_batch.py → audit_recent_docx_batch.py
验证：prepare-audit-batch → 模型逐题完成 review JSON → verify-item；大量已审核文件用 verify-review-batch 提交
交接：handoff --json
```

脚本负责格式校验、事务、检索、去重提示、审核脚手架、PDF 和状态汇总；模型仍负责图像理解、第一处实质性错误、数学推导、答案/解析审核和推荐相关性判断。

## 5. 不变量

- 推荐只使用 `verified=1`，必须显示来源和理由。
- 外部 `verified`、来源信誉、抽样和批量 SQL 不能替代逐题审核。
- `2026-07-19-g11-beijing-20` 仅免完整独立重解，仍须逐题审核并执行 `verify-item`。
- “粗心”必须有学生步骤的直接证据；不清晰内容使用 `unclear`。
- PDF 默认无答案、不打印；只有用户明确要求才附答案或打印。
- 仅导入开放授权或用户确认有权使用的材料。

完成写入后运行：

```powershell
python -B .agents\skills\math-error-notebook\scripts\notebook.py handoff --json
```

该输出已经包含主库哈希、完整性、验证数量、主要未验证问题、复习任务、Git 状态和默认打印规则，可直接用于下一智能体交接。
