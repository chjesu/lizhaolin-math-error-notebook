# 工作交接（Kimi，2026-07-23 午后）

> 本文件接续 `HANDOVER-2026-07-22-kimi.md`；项目规则仍以 `DEEPSEEK_STARTUP.md` / `PROJECT_ARCHITECTURE.md` 为准。
> 2026-07-22 晚 → 2026-07-23 中午期间的题库验证与录题工作由 Codex 会话完成，非 Kimi 所为，质量未复核。

## 1. 当前快照（handoff，2026-07-23 21:40 实测）

- canonical_path：`data/math_notebook.db`（唯一主库；`2026-07-18\new-chat-3` 同名副本严禁触碰）
- schema_version：2；integrity_check：**ok**；foreign_key_violations：**0**
- 题目：**2425**；已验证：**1615**（含 Codex 晚间并行波次约 +130，来源为上学期试卷，与 Kimi 队列无冲突）；Kimi reviewer `kimi-2026-07-23` 累计 139 条；来源：157；错题：13；到期复习：20
- 逻辑 SHA-256：见 `handoff --json` 最新值（每次落库都会变）
- 自动化测试：**37/37 通过**（`python -X utf8 -m unittest discover -s tests`）
- Git：`a80879c`（fix + docs 两个 Kimi 提交），工作区无跟踪文件脏改

## 1.1 Kimi 7-23 下午验证波次（+32 verified，1353→1385）

- **豁免批尾部 18 题全清**（`data/audits/2026-07-23-kimi-exempt-tail/`）：北师大实验阶段测试一 16 + 二中第六学段 1 + 八十中 11 月期中 1。15 corrected / 1 pass（knowledge line-circle→space-vectors 修正 ×6、feature solution→single-choice 修正 ×7、答案全角标点规范化 ×1）+ **2 reject（查重拦截）**：Q-fe90fcfdd3fe、Q-3474fac464e8 分别与已验证 Q-fa3c231ea34d、Q-11c846fb8a1c 同题。其中 Q-bfe9939d35ee（立方体多结论）按完整独立推导复核，答案 ①② 正确。
- **二中校模全卷收官**（`data/audits/2026-07-23-kimi-bj2-preexam/b02~b05/`）：7-22 导入批次**不豁免**，12 题全部完整独立推导（11 corrected / 1 pass），答案均与库中一致。修正要点：Q-89272c85abf7 题干 OCR 误字“1n2”→$\ln 2$；Q-e8e541b3102d 库中解析 h(0) 系 h(1) 笔误（review_note 注明，未改正文）；多题 knowledge/feature 冗余误标修正。该来源仅剩 Q-46d912b38c4d（codex 今日已 reject 的同题重复）在队列中，属设计如此，**此来源视为完成**。
- 亮点复核：Q-b267f4ea5127（数列压轴）(1) 枚举验证 4 组解无遗漏、(2) 逼迫链成立；Q-e1b2236e71a0（椭圆）(2) 几何转化 PE⊥PF + 常数方程组复算，P(0,1) 正确；Q-08ce9cf38ab1（翻折多结论）①√5/15≠√5/10、②π/4、③恒约 26.6°、④圆轨迹逐条复核。
- reviewer 均为 `kimi-2026-07-23`；决策文件含完整独立解答或豁免核查说明。

## 1.2 Kimi 7-23 下午第二波：八中热身练全卷 21 题收官（+21 verified，1385→1406）

- **北京市第八中学2026届高三第二学期热身练数学试题 21/21 全清**（`data/audits/2026-07-23-kimi-bj8-warmup/b01~b05/`）：7-22 导入批次**不豁免**，全部完整独立推导。20 corrected / 1 pass（Q-f786650cffb6 椭圆轨迹压轴，轨迹方程与 |AC|=|BC| 证明复算一致）。
- 答案/解析实质修正 5 处：Q-cd7519d0301a（P_m 数列压轴）答案 (ii) $a_l$ 系 $a_1$ 笔误；Q-a8b3cba4e605 解析漏等号；Q-0e59a15f3933 条件②分支「△ABC 为直角三角形」系「△BCE」笔误（答案+解析两处）；Q-76cffee4e374 解析三处笔误（全角等号、数形结合、综上所述）；Q-f2ce1155e891 答案漏写定义域、\cos 漏反斜杠。
- 标签修正惯例延续：feature solution→single-choice ×9；knowledge 误标删除（counting-binomial、line-circle ×3、plane-vectors、derivatives ×2、derivative-applications、trig-graphs、solid-geometry、conic-ellipse ×2、sets）；冗余但不错的一律保留。
- 复算亮点：Q-f88c87afae0f 立方体双截面 8 面体表面积 16 全要素重算；Q-cd7519d0301a (ii) c_n 单调性+最终常值 1 链条独立推出 m=2、$a_1=2^l$；Q-e1b124f9b51d 传感器概率三问全验（含第(3)问 $p>\frac89$ 边界）。
- **注意**：b01 曾出现 verify-review-batch 显示成功但 DB 未落库的假完成（首轮 final-reviews 未生成却报 5/5），已重跑确认；后续每波均以 handoff/audit-summary 计数复核为准。

## 1.3 Kimi 7-23 下午第三波：八十中考前热身全卷 21 题收官（+21 verified，1406→1427）

- **北京市第八十中学2025-2026学年第二学期考前热身高三数学试卷 21/21 全清**（`data/audits/2026-07-23-kimi-bj80-preexam/b01~b05/`）：不豁免，全部完整独立推导。16 corrected / 5 pass。
- 答案/内容实质修正 3 处：Q-11ca5212f1ba 答案斜杠并列「$-\frac{1}{4}$/$-0.25$」规范化；Q-63bf6fc6633b 解析向量箭头组合符乱码（$2a^{⃗}·b^{⃗}$）改 $\vec{a}\cdot\vec{b}$ 规范写法；Q-cfbfbcbc9569 题干 $xln(x+1)$ 漏反斜杠改 $x\ln(x+1)$。
- 标签修正：feature solution→single-choice ×9、误标 parameter-range/derivative-analysis 各删 1；knowledge 误标删除（conic-ellipse ×2、line-circle ×3、solid-geometry、derivatives、algebra-operations→sets/counting-binomial 精确化各 1），Q-4d803c2e2d1a 补标 space-vectors。
- **查重记录**：Q-77552b90c14d（三角条件选择压轴）与 Q-81ba65d79655（清华附中统练2，相似度 0.943）**同题跨卷收录**——本波保留 Q-77552b90c14d，**Q-81ba65d79655 进入其来源波次时必须 reject 并引用本条**（写入 §5 备忘）。Q-3ca3db47aa9d 的两个近邻（0.879/0.844）与 Q-77552b90c14d 的另一近邻 Q-27892819123e（0.867）均为变体（二项式/三角函数式不同），保留。
- 复算亮点：Q-226209f85cb9（构造数列压轴）n≤9 模最大/最小论证链补齐 + A₉ 全类三元抽查；Q-7dcb1cb377a6 ④用中心对称构造反例 |AB|² 最小 8√2-8<4；Q-24bcd045445f 椭圆切线 y_M−y_N 分子化简全链条；Q-7b1453ddf260 (3) 超几何单峰性 + 邻项比得 t∈{5,6,7}；Q-cfbfbcbc9569 (3) 极值点 φ(t₀)≥0 ⟺ m≥1/e 边界验证。

## 1.4 Kimi 7-23 下午第四波：八十中统练二全卷 21 题收官（+21 verified，1427→1448）

- **北京市第八十中学2025-2026学年第二学期高三一模前模拟练习数学（统练二）21/21 全清**（`data/audits/2026-07-23-kimi-bj80-tl2/b01~b05/`）：不豁免，全部完整独立推导。16 corrected / 5 pass，0 reject。
- 内容实质修正 1 处：Q-04ab5e3ea47e 答案/解析斜杠并列「$\frac{3}{2}$/$1.5$」规范为单一写法（其余 corrected 均为标签修正）。
- 标签修正：feature solution→single-choice ×9；knowledge 误标删除（conic-ellipse ×3、line-circle ×4、derivatives ×2、trig-graphs），Q-e85317b111d8 plane-vectors 精确化为 space-vectors；冗余但不错的一律保留。
- 查重记录：Q-6a2d69e6536f 近邻 Q-2093a82a2d98（0.879）已拉全文比对——该题多「奇函数」条件、答案 C 不同，属变体，两条均保留；Q-fcb9afa1f2b7（0.822<0.85）为「单调递减」反义变体。
- 复算亮点：Q-7c61d45dca10 椭圆 MN 中点恒为定点 (0,1/2) 全链条复算；Q-b6e9011dd69d 三角图象 T=3π/2、t=π/8 复核；Q-20c0168e88a7 三次曲线四结论逐条（③凹函数弦界面积<5、④联立 16x³−x²−46x+31=0 判别式 47² 全有理交点）；Q-e85317b111d8 四棱锥三问建系全算（二面角 √15/5、点面距 2√6/3）；Q-ffb4bba64398 导数压轴三问（对称性化简、(3) 韦达化简 64−8/a∈(32,64)）。

## 1.5 Kimi 7-23 下午第五波：八十中二模前测试一全卷 21 题收官（+20 verified，1448→1468）

- **北京市第八十中学2025-2026学年第二学期高三下学期二模前测试一数学试题 21/21 全清**（`data/audits/2026-07-23-kimi-bj80-emq1/b01~b05/`）：不豁免，全部完整独立推导。15 corrected / 5 pass / **1 reject**：Q-53723dcd7963 与已验证 Q-c91b57c9460d（2024 新课标 I 卷真题，0.979）同题同答，保留真题条。
- 内容实质修正 3 处（均用临时 py 脚本从 packet 全文程序化替换，防手写截断）：Q-255492c2cb03 答案+解析 `ABcos60°` 漏反斜杠补 `\cos`；Q-05f957876026 答案/解析斜杠并列「0.9/ 9/10」规范为 0.9；Q-b5851dc7f77c 解析 `xcosθ/ysinθ` 多处漏反斜杠程序替换。
- 标签修正：feature solution→single-choice ×8；knowledge 误标删除（conic-ellipse ×2、derivatives ×2、trig-graphs、sine-cosine-laws、line-circle）、精确化（algebra-operations→counting-binomial、plane-vectors→space-vectors、sets→solid-geometry）。
- 查重记录：Q-dc2a2ef2e005（解三角形周长）与 Q-f248f4a7055a（交大附中高一期末，0.959）题干(1)与三条件相同，但该题(2)求中线长（2√21 亦自洽），**变体**两条均保留；Q-05f957876026（举架填空版，k₃=0.9）与 Q-05cb7960566b（北师大附中开学考选择版，0.949，未验证）**同题跨卷**，保留本条，**Q-05cb7960566b 列入待 reject 备忘**（见 §5）。
- 复算亮点：Q-f524d79c040f（自创数列压轴）三问全验：(1) a₅∈{2,3} 枚举、(2) 反证四分支逐一推演至 1 出现、(3) 首超项推出 a_{n₀+1}=1 矛盾链；Q-b0737f03629c 椭圆+直线与圆相切 d²=2 分母因式分解全链条；Q-eb6283d9cb06 (3) 换元 u=g²∈(0,1/e²] 值域端点取舍验证；Q-17aca5ece4fc 可旋转函数 k∈[−e,0] 整数 3 个。

## 1.6 Kimi 7-23 晚间第六波：四中考前模拟全卷 21 题收官（+21 verified，Kimi 1448→1469 计入并行前口径）

- **北京市第四中学2025-2026学年第二学期高三考前模拟数学试题 21/21 全清**（`data/audits/2026-07-23-kimi-bj4-prefinal/b01~b05/`）：不豁免，全部完整独立推导。13 corrected / 8 pass，0 reject。采用 Codex 精简决策格式（标签不变时省略 codes 字段，扩展器自动回填）。
- 内容实质修正 1 处：Q-58c896e61215 解析组合符箭头乱码 `a^{⃗}/b^{⃗}` 程序替换为 `\vec{a}/\vec{b}`（同八十中波次惯例）。
- 标签修正：feature solution→single-choice ×9；knowledge 误标删除（derivatives ×2、trig-graphs、inequalities→trig-identities、counting-binomial、conic-ellipse）、plane-vectors→space-vectors ×2。
- 查重记录：Q-27892819123e（三角三条件）与已验证 Q-77552b90c14d（0.853）化简后虽同为 sin(2ωx−π/6)，但 (1) 参数、(2) 条件②③、可选组合均不同，**变体**两条均保留；Q-4ca3d63fe538（玉琮）与 Q-4aebfabdfe7f（北师大实验考前热身，0.965，未验证）**题干逐字相同同题跨卷**，保留本条，**Q-4aebfabdfe7f 列入待 reject 备忘**（见 §5）。
- 复算亮点：Q-1a9eaf27ffe1（性质 P 排列压轴）(2) 奇偶性论证、(3) 配对值恰一+单射+D 不在像中全链条；Q-cf602d10cc1d 周期 4 数列 ③反例 t=2、④边界 868·7/3≈2025.33 验算；Q-ff175b74cedb 椭圆定值取 k=1 特值全链条数值验证 N(−4,−2)；Q-b65b295cc763 φ(t)=eᵗ−t 双侧约束交集 [−1,1]；Q-8efaea71f444 正方体动点四选项建系逐一（D 体积 (2t+2)/3 随 t 变）。
- ⚠️ 并行说明：Codex 会话晚间在上学期试卷来源上并行验证（21:11 起 codex-high-2026-07-23 落库），全局 verified 计数跳动属正常；Kimi 波次完成度一律以来源剩余数复核。

## 2. Codex 会话 7-22 晚～7-23 午进展（非 Kimi 工作，仅记录观测）

- 验证：1272 → 1353（+81），reviewer 为 `codex-medium/high-2026-07-23`，最后一批落库 2026-07-23T12:50:38。
- 审计目录：`data/audits/2026-07-23-current-high/`（bj101-wenquan-g12-b01/b05、bj11-recursive-final、bj2-g12-preexam-b01、gqm-g11-midterm-b02、qf2-completion）与 `data/audits/2026-07-23-current-medium/`（bj101-wenquan-g12-b02/b03/b04、gqm-g11-midterm-b03/final）。
- 7-22 晚补录 5 道错题（ERR-20260722-1121d278 / 15381503 / 3010048a / 5d67e136 / b627f86b），均为椭圆含参类，错因多为 incomplete_cases。
- **因额度中断**：7-23 13:05 正在调试练习卷公式渲染（现场脚本 `tmp/pdfs/inspect_math_runtime.py`），未收尾。

## 2.1 Codex 7-23 下午新增判题照片 OCR（Kimi 已核查并修复 GPU 链路）

- Codex 两提交：`646fc8b`（RapidOCR 离线预检 `photo-preflight`，缓存 + 小预览 + 疑难裁剪）与 `21b6e40`（隔离进程 Paddle GPU 公式识别 `paddle_formula_worker.py`，仅处理小裁剪、输出不可信 LaTeX 候选、不写主库）。设计目标：判题时模型先读 OCR 文本/公式候选、只看小裁剪，减少 token 消耗。SKILL.md / PROJECT_ARCHITECTURE.md 已同步，判题固定流程改为 `photo-preflight → 模型按需查看小图 → grade-preview → grade-commit`。
- Kimi 核查（7-23 傍晚）：新增测试全过 **41/41**；RapidOCR 主链路实测可用（779 字、6 裁剪、预览生成、缓存命中正常）。
- **已修复：GPU 公式阶段初始为 fallback**。根因：`.runtime/paddleocr/nvidia/` 下 8 个 CUDA 包只剩 dist-info、payload 全缺（仅 cudnn 完整），报 `Could not locate cublasLt64_12.dll`。修复：按 dist-info 版本**单笔 pip 事务**重装 8 包（cublas 12.6.4.1 / cuda_runtime 12.6.77 / cudnn 9.9.0.52 / cufft 11.3.0.4 / curand 10.3.7.77 / cusolver 11.7.1.2 / cusparse 12.5.4.2 / nvjitlink 12.9.86）到 `.runtime/paddleocr`。⚠️ 教训：`pip --target --upgrade` 分批安装会因共享 `nvidia` 命名空间互相清空对方文件，必须一笔装全。
- 修复后实测：`--formula-ocr paddle` 与默认 auto 均 `status: ok, device: gpu:0`（RTX 4060 Ti，sm_89），6 个公式候选、init 5.8s + predict 24.5s、显存 598MB；auto 模式能力变化自动重建了旧 fallback 缓存，符合设计。本机 `nvidia-smi` 报 NVML Unknown Error 但 CUDA 链路（nvcuda + pip CUDA 库）不受影响。
- 运行时修复属本地环境（.runtime 在 .gitignore），无需提交；代码无改动。

## 3. Kimi 本次完成：修复数学简写渲染中断点（核心交付）

**问题**：题库存在 TeX 合法但 mathtext 拒绝的简写（单 token 参数不带花括号），渲染失败静默退回文本式写法——合并练习卷第 4 页错题原题出现 `e=frac√105`（户主 7-20 明令禁止）。

**修复**（`.agents/skills/math-error-notebook/scripts/practice_sheet.py`）：

1. 新增 `_normalize_math_args()` + `_read_math_token()`：把 `\frac12`、`\frac{\sqrt{10}}5`、`\dfrac\pi2`、`\binom n2`、`\sqrt3`、`\vec a` 规范化为带花括号形式；`\sqrt[n]{...}` 不支持则保持原样走文本回退。
2. 接入点：`_render_math_image()` 渲染前规范化（缓存 key 用规范化后文本）；`_latex_to_text()` 开头同步规范化，文本回退路径也不再泄漏 `frac`。
3. 新增 `_CJK_MATH_PUNCT`：数学段内全角标点 `，；：（）．` 转半角后再判 CJK，避免整段因一个全角逗号退回 `(√2)/(2)` 式文本；含真实中文的段仍回退纯文本（原设计不变）。
4. 回归测试 3 条（`test_practice_sheet_normalizes_bare_math_args` / `..._renders_tex_shorthand_as_images` / `..._renders_fullwidth_punct_math_as_image`），全量 **37/37 通过**。

**PDF 重生成**（均未打印）：

- `practice/2026-07-23-yesterday-five/pdfs/ERR-20260722-*.pdf` ×5（每份 1 页 2 题，无答案页）
- `output/pdf/李兆霖数学错题本-2026-07-22新增错题推荐练习.pdf`（5 页合并版，页序 b627f86b→15381503→1121d278→3010048a→5d67e136，与 Codex 原合并顺序一致）
- 验证：pypdf 全文扫描无 `frac`/`√` 残留；LibreOffice 转 PNG 目检第 2/3/4 页，分数线、根号均为书面格式。

## 4. 户主长期偏好（不变，再次强调）

- PDF 数学一律 mathtext 图片渲染，禁止 `√()`、`(a)/(b)`、`frac` 等文本式写法；练习卷默认无答案、不打印；孩子做完拍照判题录 `attempt`/`review`。
- 判题铁律：区分印刷/手写、指第一处实质错误、不清晰用 `unclear`、`careless` 需直接证据。

## 5. 未完成事项

1. **未验证题 810 道**（2425−1615，含 Codex 并行成果）：`2026-07-19-g11-beijing-20` 豁免批**已全部清零**；其余全部须完整独立推导（7-17 老批次 287、7-18 新课标 I 卷 3、7-22 导入批）。Codex 今日两个 current 批次目录 review 已全部落库（47/47），无遗留。已 reject 的题（如 Q-46d912b38c4d、Q-fe90fcfdd3fe、Q-3474fac464e8、Q-53723dcd7963）仍在未验证队列属设计如此，勿重复审。**待 reject 备忘三条：① Q-81ba65d79655（清华附中统练2）与已验证 Q-77552b90c14d 同题；② Q-05cb7960566b（北师大附中开学考举架选择版）与已验证 Q-05f957876026 同题；③ Q-4aebfabdfe7f（北师大实验考前热身玉琮题）与已验证 Q-4ca3d63fe538 同题——进入对应来源波次时 reject 并引用保留条**。下一来源：四中开学测试（20 题）→ 育才 2 份 → 北师大附中开学考/北师大实验考前热身（含备忘②③）等。
2. **到期复习**：用 `due --json` 拉取、`tmp/review_sheet.py` 生成复习卷。
3. 环境备忘不变：无 pytest/fitz/poppler；bash 双引号吞 `$`；用户开着 PDF 会 PermissionError（本次用临时文件 `_merged-new.pdf` 再 replace 规避）；PDF 转 PNG 目检可用 `soffice --headless --convert-to png`（仅首页，单页 PDF 正好）。
