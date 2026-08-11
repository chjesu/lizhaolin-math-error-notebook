# 李兆霖数学错题本：DeepSeek / Codex++ 快速启动

本文件只用于无法直接判断启动方式时。项目的实时状态、任务规则和下一步命令由权威脚本生成，避免每个智能体重复读取长交接材料。

## 1. 固定入口

- 正式项目名称：`李兆霖数学错题本`
- 项目根目录：`C:\Users\Administrator\Documents\Codex\2026-07-17\new-chat-5\math-error-notebook`
- 唯一主库：`data/math_notebook.db`
- 唯一业务入口：`.agents/skills/math-error-notebook/scripts/notebook.py`
- PDF/打印入口：`.agents/skills/math-error-notebook/scripts/practice_sheet.py`

不得扫描、复制、合并、覆盖或改用其他同名数据库，不得直接 SQL 修改验证状态。

## 2. 每次启动以 UTF-8 模式运行两条预检命令

```powershell
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py doctor --json
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py agent-context --task <任务> --json
```

读取项目文本统一写成 `Get-Content -Raw -Encoding UTF8 <path>`；不要先按系统默认编码读取再猜测重试。本项目不依赖 PowerShell 脚本执行策略或用户级 Profile。

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
验证：prepare-audit-batch → 模型逐题输出精简决策 → prepare-review-batch → verify-review-batch
每日复习：daily-review-packet → 补齐缺少的已复核推荐 → practice_sheet.py --daily-packet
可恢复任务：workflow-start → workflow-update → workflow-status
交接：handoff --json
```

脚本负责格式校验、事务、检索、去重提示、审核脚手架、PDF 和状态汇总；模型仍负责图像理解、第一处实质性错误、数学推导、答案/解析审核和推荐相关性判断。
新接入模型先按任务调用 `behavior-cases --category grade|verify|recommend --json`；只在需要时读取单个完整案例，避免反复加载长规则。

## 5. DeepSeek 无视觉能力时的照片判题流程

项目已经提供两级本地 OCR，OCR 在本机运行，DeepSeek 只读取精简 JSON：

- RapidOCR：方向校正、印刷题目正文、题库编号、预览和疑难裁剪。
- PaddleOCR FormulaNet：对疑难小裁剪生成数学公式 LaTeX 候选。
- 三科错题本共用整机级 OCR 执行槽。Kimi、DeepSeek 或多个 Codex 会话同时调用时应等待 `photo-preflight` 自动排队，不得另起 OCR 脚本或并行加载模型；取得执行槽后脚本会再次检查缓存。可从 `doctor.ocr_runtime.concurrency` 查看实际共享锁。

先运行：

```powershell
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py photo-preflight <图片路径> --formula-ocr auto --json
```

若本机视觉模型可用，不要让 DeepSeek 直接反复接收整张照片，也不要让本地模型判题。先读取固定契约，将其中的提示词与单页预览/疑难裁剪交给本地模型，再校验返回的纯 JSON：

```powershell
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py photo-vlm-contract --json
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py photo-vlm-validate <本地模型响应.json> --packet <ocr-packet.json> --page 1 --json
```

只有 `photo-vlm-validate` 输出的精简证据可以进入 DeepSeek 判题上下文。本地模型不得返回对错、标准答案、解题过程、错误原因或思维链；质量门为 `visual_review_required` 时，必须查看对应预览/裁剪或交给具备视觉能力的模型确认。

日常判题直接读取命令返回的精简 `ocr_pages`、`question_ids`、预览路径和裁剪选择器，不要再次读取完整 `ocr-packet.json`。如果识别到题库编号，优先读取主库中的标准原题：

```powershell
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py question <题库编号> --compact --json
```

随后按以下质量门分流：

1. **可由 DeepSeek 继续判定**：印刷题干清晰；或已由题库编号取得标准原题；学生答案是无歧义的选择、数字或短文本；OCR 各处内容彼此一致；题意不依赖图形。
2. **必须标记 `requires_visual_confirmation` 并停止保存**：关键公式、负号、指数、分母、等号或定义域置信度不足；连续手写推导；坐标图、几何图、辅助线；圈选不清；涂改或手写覆盖印刷文字；OCR 与题库原文或不同 OCR 候选冲突。
3. FormulaNet 输出只是**不可信的 LaTeX 定位候选**。未经视觉复核，不得把它当作原题、学生步骤、答案或判错依据。
4. OCR 不能替代数学审核。通过质量门后，DeepSeek 仍须完成逐题判定、首个实质错误定位和正确推导；不能确认的内容使用 `unclear`，不得补造。
5. 只要存在 `requires_visual_confirmation`，不得执行 `grade-commit`，也不得保存错题或给出确定的对错结论；应请求清晰局部图、用户提供文字版答案，或交给具备视觉能力的智能体复核。

推荐执行链：

```text
照片
  → photo-preflight（本地 RapidOCR + 可选 FormulaNet）
  → 识别到题库编号时读取 question --compact
  → DeepSeek 可判定性质量门
      → 安全题：数学推理 → grade-preview → grade-commit
      → 疑难题：requires_visual_confirmation → 停止保存并请求视觉复核
```

这套流程的目标是减少图片直接输入 Token，而不是降低判题证据标准。

## 6. 不变量

- 推荐只使用 `verified=1`，必须显示来源和理由。
- 外部 `verified`、来源信誉、抽样和批量 SQL 不能替代逐题审核。
- `2026-07-19-g11-beijing-20` 以及自 `2026-07-20`（含）起入库的高质量试卷题可免完整独立重解，但仍须逐题审核并执行 `verify-item`；使用 `audit-summary` 和 `--simplified-only` 获取权威范围，遇到疑点仍须独立推导。
- “粗心”必须有学生步骤的直接证据；不清晰内容使用 `unclear`。
- PDF 默认无答案、不打印；只有用户明确要求才附答案或打印。
- 仅导入开放授权或用户确认有权使用的材料。

完成写入后运行：

```powershell
python -B .agents\skills\math-error-notebook\scripts\notebook.py handoff --json
```

该输出已经包含主库哈希、完整性、验证数量、主要未验证问题、复习任务、Git 状态和默认打印规则，可直接用于下一智能体交接。
