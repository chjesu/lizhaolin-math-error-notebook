# 工作交接（Kimi，2026-07-19 晚 → 2026-07-22 上午）

> ⚠️ 本文件已被 `HANDOVER-2026-07-23-kimi.md` 接续：7-22 晚起 Codex 会话推进了大量验证（已验证 1353），Kimi 于 7-23 修复了 mathtext 简写渲染缺陷。§1 快照（864 verified）已过时，仅供回溯。

## 1. 交接快照（bank-info，2026-07-22 11:30 实测）

- canonical_path：`data/math_notebook.db`（唯一主库，未触碰其他同名库）
- schema_version：2；integrity_check：**ok**；foreign_key_violations：**0**
- 题目：**1383**；已验证：**864**（接手时 803，**+61**）；未验证：**519**
- 错题：**8 条**（接手时 7，+1）；attempts：5 条；复习记录：8 条 correct
- 逻辑 SHA-256：`ffab9434f82793a0a95a04d081faf348175d18107bef30fa5a0d243e1149ad7a`
- 自动化测试：**23/23 通过**（`python -m unittest discover -s tests`）

## 2. 题库验证（+61 题）

| 批次 | 范围 | 数量 | verified 变化 |
|---|---|---|---|
| 试点批 | 陈经纶 10 月月考前 10 题 | 10 | 803→813 |
| 批次 2 | 陈经纶 10 月月考余 9 + 二中第一学段 11 | 20 | 813→833 |
| W1 | 二中第一学段余 10 + 清华附中朝阳高二下期中 21 | 31 | 833→864 |

- 全部走 `audit-item → 逐题审 → verify-item` 流程，审核 JSON 存 `data/audits/reviews/`，无批量 SQL。
- 前两批（30 题）按原标准完整独立推导。
- **W1 起执行户主 2026-07-21 指示**：`2026-07-19-g11-beijing-20` 批次（20 套北京名校卷）来源可信，免完整独立推导；改为核查题干完整、查重、答案与解析逻辑自洽，review_note 已如实注明核查深度。**该豁免仅限此批次**，其他来源（FrankieYao、丰台/东城/海淀老卷等 310 题）仍须完整独立推导。
- 系统性发现：该批次大量题 knowledge 误标 `line-circle`、单选题 feature 误标 `solution`，已在各题 correction 中修正。

## 3. 错题与教学闭环

- 新录错题 **ERR-20260720-a7a5c990**：$A(-1,0)$、$B(1,0)$，$\vec{AQ}\cdot\vec{BQ}=0$，$P$ 在 $x-y+2=0$ 上，$|PQ|_{\min}$。学生答 $\frac{\sqrt2}{2}$（=点 A 到直线距离，把动点 Q 当定点）；正确 $\sqrt2-1$。错因 `method_choice`，原照片已归档。
- 人工复核推荐 3 题（均已验证）：Q-15322ab8b191（首师大附中）、Q-e7671f2c85c2（171 中学）、Q-2f3d05971f1f（人大附中）。
- attempts：题1 ✅、题2 ✅、题3 先"不会做"→ 讲解后重做 ✅；巩固题 Q-09b486aac9a1 推导 $[0,4)$ 全对但**选项字母写成 C**，按 `careless` 记错（证据充分，防"会而选错"）。
- 讲解记录：Q-612d7b510f0a（含参直线过定点 + 弦心距最值）、极化恒等式专题。
- 到期复习 16 条（去重 8 题）：**8/8 全对**，`review --result correct` 全部录入。

## 4. 工具改进（practice_sheet.py 等）

1. **题图嵌入 PDF**：新增 `split_stem_images()`，题干/错题原题/解析中的 `![原题图](...)` 渲染为版心内等比缩放图片（≤150×78mm）；文件缺失才显示"［题图缺失，见原卷］"。
2. **默认无答案页**（户主 2026-07-20 指示）：`create_pdf` 默认 `include_answers=False`，CLI 改 `--with-answers`；config `answers_after_questions=false`；页眉改为"全部做完后拍照发给我判"；打印逻辑同步（无答案页直接打，有答案页仍只打题目页）。SKILL.md 已同步。
3. 新脚本（tmp/）：`review_sheet.py`（到期复习卷生成）、`single_question_pdf.py`（单题排版 PDF）。
4. 新增测试 3 项：图片引用拆分、PDF 实际嵌图、默认无答案；全量 23 项通过。

## 5. 生成的 PDF（output/pdf/，均未打印）

- `ERR-20260720-a7a5c990-practice.pdf` 等 4 份练习卷（c1e70361 / ed254589 / f2c56f1e / a7a5c990，纯题目版）
- `Q-09b486aac9a1-polarization.pdf`（极化恒等式巩固单题）
- `review-due-2026-07-21.pdf`（到期复习卷 8 题）

## 6. 户主长期偏好（后续会话必须遵守）

- PDF 数学公式一律 matplotlib mathtext 图片渲染（书面根号/分数线），**禁止 √() 文本式写法**；含中文段回退纯文本。
- 练习卷**默认不附答案、不打印**；孩子做完拍照发来，由我判题并录 `attempt`/`review`。
- 判题铁律不变：区分印刷体与手写、指第一处实质错误、不清晰用 `unclear`、`careless` 需直接证据。
- 主库唯一：`data/math_notebook.db`；`2026-07-18\new-chat-3` 下同名 DB 是 DeepSeek 会话副本，**严禁使用/合并/覆盖**。

## 7. 未完成事项

1. **未验证题 519 道**：
   - `2026-07-19-g11-beijing-20` 批次剩 **209 道**（二中第五/第六学段、五中、八十中×2、四中、陈经纶六月、首师大×2、人大附中×2、北师大实验），按户主豁免标准继续；
   - 其他来源 **310 道**（FrankieYao 41、西城 21、丰台复习检测 57、人大附中 2021-2022 20、东城 37、朝阳 18、海淀 33、人教版 10、首师大 2020-2021 9、模拟卷 10、新课标 I 卷 3 等），须完整独立推导。
   - 历史已知"拦下"记录见 `data/audits/2026-07-18-unverified-repair-progress.md`（题干残缺卷需回原 PDF 修复，不要强行放行）。
2. **到期复习**：stats 显示当前 `due_reviews=10`（复习周期滚动产生），需要时用 `due --json` 拉取、`tmp/review_sheet.py` 生成复习卷。
3. **"安装 kimicode"**：户主 2026-07-21 17:05 提及，意图未确认（装 CLI 还是发错消息），下次先问清。
4.  bash 环境注意：双引号会吞 `$`（LaTeX 测试须用脚本文件）；用户打开 PDF 会锁文件（PermissionError 时请其关闭）；无 poppler/fitz，PDF 目检可用 pypdf 文本提取 + XObject 计数代替。
