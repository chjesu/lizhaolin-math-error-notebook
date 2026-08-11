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

默认 `photo-preflight` 只在本地完成 EXIF 方向、透明底白底化和尺寸压缩，不运行 RapidOCR、PaddleOCR 或本地 Qwen，也不占用 OCR 锁：

```powershell
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py photo-preflight <图片路径> --task grade --json
```

返回 `review_route=remote_model_visual_review` 时，必须把全部 `preview_paths` 交给具备图片能力的 Codex/Kimi 等远端模型直接查看。DeepSeek 本身没有视觉能力时不得继续判图、不得依据历史 OCR 或文件名猜测，也不得执行 `grade-commit`；应把任务和预览路径交接给视觉模型。视觉模型完成判题后，DeepSeek 可以继续处理结构化保存、推荐和 PDF 等纯文本步骤。

只有明确诊断 OCR 或本地服务时才使用：

```powershell
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py photo-preflight <图片路径> --preflight-mode ocr --formula-ocr off --vision-mode off --json
```

显式 OCR 模式仍经过三科共享执行锁；不得另起脚本绕过。`--formula-ocr paddle` 与 `--vision-mode required` 也必须同时指定 `--preflight-mode ocr`。

以下命令仅用于诊断合同或单独校验历史响应，日常流程无需人工拼接：

```powershell
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py photo-vlm-contract --json
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py photo-vlm-validate <本地模型响应.json> --packet <ocr-packet.json> --page 1 --json
```

历史本地模型响应只有通过 `photo-vlm-validate` 后才能作为辅助转录。本地模型不得返回对错、标准答案、解题过程、错误原因或思维链；它不再属于日常判题链路。

日常判题由远端视觉模型直接读取命令返回的预览路径，不要再次读取完整 `ocr-packet.json`。如果视觉模型看见题库编号，可优先读取主库中的标准原题：

```powershell
python -X utf8 -B .agents\skills\math-error-notebook\scripts\notebook.py question <题库编号> --compact --json
```

远端视觉模型仍须逐题分开印刷题目与手写内容，定位第一处实质错误；关键公式、负号、指数、分母、图形或涂改看不清时必须请求清晰局部图，不得补造。DeepSeek 只能在视觉模型已经输出结构化可见证据和判题结果后接手后续流程。

推荐执行链：

```text
照片
  → photo-preflight（仅方向/白底/尺寸控制）
  → 远端视觉模型打开全部 preview_paths 并逐题判定
  → 识别到题库编号时读取 question --compact
  → grade-preview → grade-commit
  → 看不清：停止保存并请求清晰局部图
```

这套流程用较小且清晰的标准化图片控制输入成本，同时避免本地视觉/OCR的等待时间，不降低判题证据标准。

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
