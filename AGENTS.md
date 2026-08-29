# 李兆霖数学错题本项目规则

- 项目文本编码固定为 UTF-8。Windows PowerShell 读取文本必须显式使用 `Get-Content -Encoding UTF8`，不得先按系统默认编码读取再猜测重试；Python 启动命令使用 `python -X utf8 -B`，文件读写显式指定 `encoding="utf-8"` 或 `utf-8-sig`。禁止用无编码参数的重定向改写项目文本。
- 通过 Codex++、DeepSeek 或其他外部模型运行时，先运行 `notebook.py doctor --json` 和与任务匹配的 `notebook.py agent-context --task <task> --json`；仅在预检失败或输出要求时读取 `DEEPSEEK_STARTUP.md`/专项参考。
- 修改或新建功能前先查 `PROJECT_ARCHITECTURE.md`，优先扩展现有权威入口，禁止创建平行数据库层、推荐器、验证器或通用打印器。
- 错题、题库、推荐和复习任务使用项目级 `math-error-notebook` Skill，并通过其脚本操作。
- 唯一主库为 `data/math_notebook.db`；不得发现、合并、复制覆盖或改用其他同名库。
- 照片批改须保存结构化记录；不清晰项明确说明；数学用 LaTeX；无直接证据不得归因“粗心”。
- 判题输出标准：逐题给出判定。若题目有错误或部分正确，必须展示完整原题、学生作答、第一处实质错误、正确解析过程和最终答案；若存在两种及以上真正不同且适用的解题思路，分别列出。原题、符号或图形不清晰时不得补造，须明确指出并请求清晰局部图。完全正确时给出简洁核验依据即可。每次判题回复末尾必须给出“下一步”行动指令，明确孩子现在做什么、做几题或做到什么程度、完成后提交什么；不得只给“继续练习”“加强复习”等泛泛建议。全部正确时也要明确本次是否结束以及下一复习日期或任务。
- 凡照片或文档可由标题、页脚、错题编号、题库编号、复习日期或阶段信息识别为项目生成的每日错题复习 PDF，即使用户只说“判一下”，也必须自动视为整包复习判题：定位并校验生成该 PDF 的原始 packet，将每个错题组的作答与其 `error_id + cycle + stage` 关联，并通过 `finalize-review-packet` 在同一事务中写入推荐题作答、复习结果、下一阶段和 packet 台账，不得拆成独立的 `attempt`/`review`，也不得等待用户另行申请完成阶段。`correct` 推进当前阶段，`partial`/`wrong` 按规则启动适应性周期，`unclear`/`not_attempted` 只记录且不推进。若同日期 packet 已重生成、内容不匹配或无法唯一恢复原始 packet，必须停止阶段写入并报告，不得猜测关联或改用当前同名 packet 强行结算。
- 推荐仅限已验证题并显示来源/理由；默认生成不附答案的 A4 PDF，不打印。仅在用户明确要求时附答案或调用打印机。
- `2026-07-19-g11-beijing-20` 批次，以及自 `2026-07-20`（含）起入库的高质量试卷题，经用户确认可免除每题的完整独立重解；但仍须逐题核查题干、选项、答案与解析逻辑自洽、标签、来源和重复项，发现疑点时恢复独立推导，并通过 `audit-item → verify-item` 正式记录。用 `audit-summary` 查看可简化数量，用 `audit-queue/prepare-audit-batch --simplified-only` 筛选日期范围。
- 不满足上述条件的题，外部 `verified`、来源信誉、抽样或批量 SQL 均不能替代题干、答案、解析、标签、来源和重复项的逐题独立审核及 `verify-item`。
- 仅导入开放授权或用户确认有权使用的材料；不得绕过登录、付费墙或访问限制。
- 可机械化步骤必须优先调用现有脚本：照片错题先 `photo-preflight`，仅在本地做 EXIF 方向、透明底白底化、尺寸压缩和内容哈希缓存，再由 `codex_task_router.py run --task grade-photo` 选出的具备视觉能力模型查看全部相关 `preview_paths`，之后执行 `grade-preview → grade-commit`。项目不提供本地 OCR 或本地视觉模型判题入口；文本模型（包括无视觉能力的 DeepSeek）不得假称看图，必须交接给具备视觉能力的路由任务。候选推荐用 `recommend-packet`，审核准备用 `prepare-audit-batch`，模型精简决策用 `prepare-review-batch` 扩展，大量已完成的逐题审核用 `verify-review-batch` 提交，交接用 `handoff`；按日期导入 DOCX 用 `scripts/import_recent_docx_batch.py`，结构预检用 `scripts/audit_recent_docx_batch.py`。不得让模型重复拼装这些固定结构，也不得用批处理放宽逐题验证质量门。
- 为减少 token 消耗，已由模型形式化为 SymPy 表达式的代数、方程、恒等式和代入检查，优先批量调用 `python -B scripts/symbolic_precheck.py <checks.json>`；默认只把 `fail`/`unknown` 项交回模型扩展推理。`pass` 只证明提交的形式化表达式成立，不替代题意、图形、定义域、分类、标签、来源、重复项及 `audit-item → verify-item` 复核。
- 本项目不在 `.codex/config.toml` 中固定 GUI 模型；新任务继承用户级、受管默认或用户显式选择，GUI 智能体只负责编排。判题、题目内容/标签判断、推荐复核、审核验证、修复、生成和争议裁决等一切需要模型判断的有边界任务，默认且必须调用 `.agents/skills/math-error-notebook/scripts/codex_task_router.py`。路由器在调用前按任务和显式风险一次选定模型：标签、推荐复核和高质量简化审核使用 Luna/medium，苏格拉底引导使用 Terra/medium，判题、复习判定、完整推导、修复、生成和争议裁决使用 Sol/high（争议裁决为 xhigh）；低置信度或不完整结果直接阻断，不再串行改用第二个模型重跑。DOCX/PDF 提取、结构预检、去重、事务写入等确定性步骤仍直接使用现有脚本，不调用模型。路由结果只读，正式写库仍只能经过现有质量门；仅当路由器不可用且已明确告知用户时，才允许当前 GUI 模型临时直接承担模型判断。详细规则见 `MODEL_ROUTING.md`。
