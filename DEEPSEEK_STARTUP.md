# DeepSeek / Codex++ 启动与交接要求

你正在操作“高中数学智能错题本”项目。开始任何任务前，必须完成本文件的预检；未完成前不要修改文件、数据库或打印任务。

## 1. 固定项目和唯一主库

- 项目根目录：`C:\Users\Administrator\Documents\Codex\2026-07-17\new-chat-5\math-error-notebook`
- 唯一活动题库：`data/math_notebook.db`
- 只使用项目级 Skill：`.agents/skills/math-error-notebook/`
- 不得扫描、复制、合并、覆盖或改用其他目录中的同名数据库。
- 不得用 `--db` 指向其他数据库。
- 不得递归读取 `data/audits/`、`data/imports/` 或整个题库；优先调用精简 CLI。

## 2. 必读文件

按顺序完整读取：

1. `AGENTS.md`
2. `.agents/skills/math-error-notebook/SKILL.md`
3. `data/CANONICAL_QUESTION_BANK.md`
4. `PROJECT_ARCHITECTURE.md`（修改或新建功能前必须先查功能/脚本索引）

只有在执行导入、来源修复或题目验证时，再读取：

` .agents/skills/math-error-notebook/references/import-and-verification.md`

## 3. 只读预检

先运行以下命令，不要执行写入：

```powershell
python -B .agents\skills\math-error-notebook\scripts\notebook.py bank-info --json
python -B .agents\skills\math-error-notebook\scripts\notebook.py audit-summary --json
```

必须确认：

- `canonical_path` 指向本项目的 `data/math_notebook.db`
- `schema_version` 为 `2`
- `integrity_check` 为 `ok`
- `foreign_key_violations` 为 `0`

2026-07-22 交接快照：题目 `1383` 道，已验证 `864` 道，未验证 `519` 道，错题记录 `8` 条。若实际数量不同，先报告差异及可能原因，不得通过复制旧库恢复或覆盖。

预检完成后，先向用户输出以下回执，再开始任务：

```text
已读取项目规则和项目级 Skill。
唯一主库：<canonical_path>
完整性：<integrity_check>；外键异常：<foreign_key_violations>
题目：<questions>；已验证：<verified_questions>；未验证：<计算值>
本次任务：<任务概述>
预计写入：<无/具体内容>
```

## 4. 不可违反的质量规则

- 照片批改必须区分印刷题目和学生手写过程，指出第一处实质性错误并保存结构化错题记录。
- 关键公式、符号、条件或图形不清晰时使用 `unclear`，不得猜测。
- 只有学生步骤存在直接证据时才能判断为 `careless`。
- 数学表达使用 LaTeX。
- 推荐题只能来自 `verified=1` 的题目，并显示来源和推荐理由。
- 推荐题保存前须复核是否真正同知识点、同题型、同方法或同结构特征，不能只看宽泛标签。
- 保存推荐题后，默认生成不附答案的 A4 PDF，不调用打印机；仅在用户明确要求时附答案或打印。
- 外部记录的 `verified` 字段、来源声誉、其他模型结论和抽样检查均不构成验证依据。
- 禁止直接 SQL 或循环脚本批量修改 `verified`。
- 模型生成或补全的题目、答案、解析不能因“看起来正确”直接设为已验证。
- 只导入开放授权、官方公开或用户确认有权使用的材料；不得绕过登录、付费墙或访问限制。

## 5. 未验证题的唯一审核流程

### 5.1 指定可靠批次的审核豁免

用户已确认 `2026-07-19-g11-beijing-20` 批次来源十分可靠。该批次可免除每题的完整独立推演核算，但仍必须逐题完成以下检查：题干和选项完整、无重复题、答案与解析逻辑自洽、知识点/难度/结构特征合理、来源和授权记录完整。每题仍须通过 `audit-item → verify-item` 留下审核记录；不得批量修改 `verified`。该豁免仅限此批次，不得推广到其他来源。

### 5.2 其他来源的标准审核

每次只处理一题：

```powershell
python -B .agents\skills\math-error-notebook\scripts\notebook.py audit-item <题目ID> --out data\audits\packets\<题目ID>.json --json
```

然后独立解题，不得把数据库原答案改写后冒充独立推导。使用：

`.agents/skills/math-error-notebook/assets/question-review-template.json`

填写审核 JSON，必须核对题干、选项、图形、答案、完整解析、知识点、难度、结构特征、来源和重复项。最后逐题提交：

```powershell
python -B .agents\skills\math-error-notebook\scripts\notebook.py verify-item <题目ID> <审核JSON> --json
```

`needs_revision` 或 `reject` 只记录审核，不得提升验证状态。不得绕过该流程使用批量 SQL。

## 6. 错题批改、推荐与打印

1. 按 `assets/error-analysis-template.json` 形成结构化分析。
2. 使用 `record-error` 保存错题，不能只在对话中解释。
3. 使用 `knowledge`、`causes`、`features` 获取精简代码。
4. 使用 `recommend` 预览候选；仅在逐题复核相关性后保存。
5. 保存后运行 `practice_sheet.py <error-id>`，默认生成无答案 PDF；仅在用户明确要求时增加 `--with-answers` 或 `--print`。
6. 用户完成推荐题后使用 `attempt` 记录结果。

## 7. 每次写入后的交接

写入后重新运行：

```powershell
python -B .agents\skills\math-error-notebook\scripts\notebook.py bank-info --json
```

向用户报告：

- 实际修改的题目 ID、错题 ID或推荐记录。
- 修改前后验证数量。
- 是否生成 PDF及是否成功打印。
- 数据库完整性和外键检查结果。
- 未完成、被拒绝或证据不足的项目。

不要声称“全部完成”，除非所有目标逐项完成并有脚本输出作为证据。
