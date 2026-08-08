"""Build the reviewed correction payload for the 2026 Dongzhimen exam.

The source paragraphs come from extract_docx_omml.py.  This script only performs
deterministic question segmentation and applies the item-level review decisions
recorded below; database changes are still made exclusively by notebook.py.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from docx_parsing import clean_latex, split_options


ROOT = Path(__file__).resolve().parents[1]
IMPORT_DIR = ROOT / "data" / "imports" / "2026-07-18-dongzhimen"
AUDIT_DIR = ROOT / "data" / "audits"


QUESTION_IDS = {
    1: "Q-85651583808c",
    2: "Q-19039bf56a9b",
    3: "Q-672e2311433a",
    4: "Q-56626a7d020f",
    5: "Q-d351619514d5",
    6: "Q-3c476c8fc596",
    7: "Q-197280bd2c54",
    8: "Q-dc2dde709f21",
    9: "Q-43199e3bda13",
    10: "Q-f5912e19bbdc",
    11: "Q-038ce707841d",
    12: "Q-ae94497f5ef3",
    13: "Q-6e19dd809080",
    14: "Q-f7f67b8c4833",
    15: "Q-463cdca15881",
    16: "Q-62a404d0a05f",
    17: "Q-9252c94709b6",
    18: "Q-3defd47d6f89",
    19: "Q-0e7b61e92d43",
    20: "Q-7d3bbf6a18cf",
    21: "Q-9dea517cd4f0",
}


META = {
    1: (2.0, "单选题", ["derivatives"], ["formula_condition", "calculation"]),
    2: (2.5, "单选题", ["random-variables", "probability"], ["concept_confusion", "calculation"]),
    3: (3.0, "单选题", ["function-properties", "exponential-log", "trig-graphs"], ["method_choice", "concept_confusion"]),
    4: (3.0, "单选题", ["logic", "function-properties"], ["concept_confusion", "reasoning_gap"]),
    5: (2.5, "单选题", ["function-properties"], ["reasoning_gap", "method_choice"]),
    6: (4.0, "单选题", ["derivative-applications", "comprehensive-functions"], ["incomplete_cases", "formula_condition"]),
    7: (2.5, "填空题", ["counting-binomial"], ["algebra_transform", "calculation"]),
    8: (2.0, "填空题", ["conditional-probability"], ["formula_condition", "calculation"]),
    9: (2.5, "填空题", ["derivatives", "line-circle"], ["method_choice", "calculation"]),
    10: (3.0, "填空题", ["derivative-applications"], ["method_choice", "reasoning_gap"]),
    11: (4.0, "填空题", ["sets", "function-properties"], ["concept_confusion", "reasoning_gap"]),
    12: (2.5, "解答题", ["sine-cosine-laws", "trig-definition"], ["incomplete_cases", "formula_condition"]),
    13: (2.5, "解答题", ["derivatives", "derivative-applications"], ["calculation", "incomplete_cases"]),
    14: (3.5, "解答题", ["probability", "random-variables"], ["concept_confusion", "calculation"]),
    15: (3.5, "解答题", ["solid-geometry", "space-vectors"], ["method_choice", "reasoning_gap"]),
    16: (3.0, "解答题", ["derivatives", "derivative-applications"], ["reasoning_gap", "formula_condition"]),
    17: (4.5, "解答题", ["sequences", "inequalities"], ["method_choice", "reasoning_gap"]),
    18: (2.0, "单选题", ["sets"], ["concept_confusion", "calculation"]),
    19: (2.0, "单选题", ["inequalities"], ["formula_condition", "misreading"]),
    20: (3.0, "单选题", ["function-properties"], ["concept_confusion", "formula_condition"]),
    21: (3.0, "单选题", ["exponential-log", "algebra-operations"], ["algebra_transform", "formula_condition"]),
}


ANSWER_OVERRIDES = {
    1: "B",
    2: "D",
    3: "C",
    4: "A",
    5: "D",
    6: "D",
    7: "$15$",
    8: "$\\dfrac{3}{4}$",
    9: "$\\dfrac{1}{2}$",
    10: "$|PQ|_{\\min}=1$，此时 $x_2=1$",
    11: "②④",
    12: "（1）$\\sin C=\\dfrac{3\\sqrt{10}}{10}$；（2）$S_{\\triangle ABC}=\\dfrac92$ 或 $\\dfrac{15}{2}$",
    13: "（1）$4x+y-2=0$；（2）递增区间为 $(-\\infty,-1)$、$(2,3)$，递减区间为 $(-1,2)$；最大值为 $2e^3$，最小值为 $-2e^2$",
    14: "（1）（i）$0.44$；（ii）$P(Z=0)=0.53$，$P(Z=1)=0.35$，$P(Z=2)=0.12$，$E(Z)=0.59$；（2）$D(\\xi_1)>D(\\xi_2)$",
    15: "（1）$BF\\parallel$ 平面 $PCD$；（2）选择条件②（条件③也可唯一确定），所求正弦值为 $\\dfrac{\\sqrt6}{9}$",
    16: "（1）$f(3)>f(4)$；（2）$\\min f'(x)=-\\dfrac{2}{3e^3}$；（3）$0<a<e$ 时 $f(a)<l(a)$，$a=e$ 时相等，$a>e$ 时 $f(a)>l(a)$",
    17: "（1）$\\Omega(A)=\\{1,2,4\\}$，$\\Gamma(A)=\\{2,4,5\\}$；（2）证明见解析；（3）最小值为 $-1011$",
    18: "D",
    19: "C",
    20: "A",
    21: "D",
}


STEM_OVERRIDES = {
    11: (
        "已知函数 $f(x)$ 的定义域为 $\\mathbb{R}$。对任意 $t\\in\\mathbb{R}$，定义集合\n"
        "$M(t)=\\{y\\mid y=f(x)-f(t),\\ x\\ge t\\}$，"
        "$N(t)=\\{y\\mid y=f(x)-f(t),\\ x\\le t\\}$。给出下列四个结论：\n"
        "① 存在 $\\mathbb{R}$ 上的单调递增函数 $f(x)$，使对任意 $t$ 都有 $M(t)=N(t)$；\n"
        "② 存在 $\\mathbb{R}$ 上的单调递增函数 $f(x)$，使对任意 $t$ 都有 $M(t)\\cup N(t)=\\mathbb{R}$；\n"
        "③ 若对任意 $t$ 都有 $M(t)=N(-t)$，则 $f(x)$ 是偶函数；\n"
        "④ 若 $f(x)$ 是偶函数，则对任意 $t$ 都有 $M(t)=N(-t)$。\n"
        "其中正确结论的序号是________。"
    ),
    14: (
        "体能赛有“引体向上”和“障碍跑”两个项目，甲、乙两名选手参加。甲在两项目中取得优秀的概率分别为 $0.8$、$0.6$，"
        "乙分别为 $0.5$、$0.5$；各次、各人、各项目的结果相互独立。\n"
        "（1）进行1次体能赛：①求甲恰有一个项目优秀的概率；②设甲、乙优秀项目数分别为 $X,Y$，令"
        "$Z=\\begin{cases}X-Y,&X>Y,\\\\0,&X\\le Y,\\end{cases}$，求 $Z$ 的分布列与 $E(Z)$。\n"
        "（2）进行 $k(k\\ge1)$ 次体能赛。令 $\\xi_k=1$ 表示甲的优秀项目总数多于乙，否则 $\\xi_k=0$。比较 $D(\\xi_1)$ 与 $D(\\xi_2)$。（结论不要求证明）"
    ),
    15: (
        "如图，在四棱锥 $P-ABCD$ 中，$AD\\parallel BC$，点 $E$ 在 $AD$ 上，$PE\\perp AD$，$AD=2BC$，$F$ 为 $PA$ 的中点。\n"
        "![原题图](data/imports/2026-07-18-dongzhimen/media/image3.png)\n"
        "（1）求证：$BF\\parallel$ 平面 $PCD$；\n"
        "（2）已知 $AD=PE=2$，$AB=1$，$E$ 为 $AD$ 的中点，$AB\\perp AD$。从下列条件中选择一个，使四棱锥存在且唯一确定，并求直线 $PB$ 与平面 $PCD$ 所成角的正弦值："
        "① $PC\\perp AD$；② $BF=\\dfrac32$；③ $\\tan\\angle PCE=2$。"
    ),
    17: (
        "已知有限数列 $A:a_1,a_2,\\ldots,a_n$。对 $i,j\\in\\mathbb{N}^*$ 且 $1\\le i\\le j\\le n$，令"
        "$S(i,j)=a_i+a_{i+1}+\\cdots+a_j$，并定义\n"
        "$\\Omega(A)=\\{i\\mid \\exists j\\in\\mathbb{N}^*,\\ i\\le j\\le n,\\ S(i,j)>0\\}$，\n"
        "$\\Gamma(A)=\\{j\\mid \\exists i\\in\\mathbb{N}^*,\\ 1\\le i\\le j,\\ S(i,j)>0\\}$。\n"
        "（1）若 $A:-2,3,-4,2,-1$，写出 $\\Omega(A)$ 和 $\\Gamma(A)$；\n"
        "（2）若 $i\\in\\Omega(A)$，记 $P(i)$ 为所有使 $S(i,j)>0$ 的 $j$ 中的最小值，证明"
        "$\\{i,i+1,\\ldots,P(i)\\}\\subseteq\\Omega(A)$；\n"
        "（3）当 $n=2026$，$a_k\\in\\mathbb{Z}$、$a_k\\ge-1$，且 $\\Omega(A)\\cup\\Gamma(A)=\\{1,2,\\ldots,2026\\}$ 时，求 $\\sum_{k=1}^{2026}a_k$ 的最小值。"
    ),
}


REVIEW_NOTES = {
    1: "独立求导得 $f'(x)=-1/(1-x)$，代入 $x=-1$ 得 $-1/2$。",
    2: "三种等可能选法对应人数 $1,1,2$，期望为 $(1+1+2)/3=4/3$。",
    3: "A、B、D 均能写成 $f(x+c)+d$；$3^{1-x}$ 改变单调方向，不能仅由平移得到。",
    4: "条件推出 $f(x)=kx(k\\ne0)$，故充分；奇函数 $x^3$ 不满足比例式，故不必要。",
    5: "严格凸函数三点割线斜率递增；仅 D 的斜率 $-2<3$，且存在开口向上的二次函数通过三点。",
    6: "分别核对 $m=-1$ 与 $m\\ne-1$；由左右两段驻点位置得到 $-3<m<-2$，另有孤立值 $m=-1$。",
    7: "通项为 $\\binom6k(-1)^k x^{6-3k/2}$，常数项对应 $k=4$，系数为15。",
    8: "全概率方程 $0.9p+0.5(1-p)=0.8$，解得 $p=3/4$。",
    9: "两条垂直直线斜率分别为 $1/2,-2$；交点为 $M(-1,0),N(3/2,0)$，距离比为 $1/2$。",
    10: "令 $h(x)=e^x-x-x^2/2$；由 $e^x\\ge1+x+x^2/2$（$x\\ge0$）得最小值1，仅在 $x=0$ 取得，此时 $x_2=1$。",
    11: "用 $f(x)=x$ 验证②；偶函数换元 $u=-x$ 验证④；$x^2\\sin x$ 给出③的反例；①与单调递增性矛盾。",
    12: "正弦定理得 $\\sin C=3\\sqrt{10}/10$；余弦定理给出 $b=3$ 或5，两种三角形均成立，对应面积 $9/2$ 或 $15/2$。",
    13: "核对乘积求导及驻点 $-1,2$；比较端点与极值，最大值 $2e^3$、最小值 $-2e^2$。",
    14: "独立卷积得到 $X=(0.08,0.44,0.48)$、$Y=(0.25,0.5,0.25)$；复算分布列、期望及两次比赛的伯努利方差。",
    15: "中点构造证明线面平行；选择②建立坐标，推出 $P=(0,0,2)$，法向量可取 $(-2,2,1)$，线面角正弦为 $\\sqrt6/9$。",
    16: "分别复核 $f'$ 的符号、$g=f'$ 的导数以及 $h=f-l$ 的单调性，三问结论一致。",
    17: "复核连续和定义、最小下标证明、前缀和计数下界和达到下界的构造，最小值为 $-1011$。",
    18: "直接计算交集为 $[1,2)$。",
    19: "同加 $c^2$ 保持不等号方向；其余选项均可用零或异号反例否定。",
    20: "$1/|x|$ 为偶函数且在 $(0,\\infty)$ 上递减；其余选项至少一项性质不满足。",
    21: "换成以 $m$ 为底的对数得 $-\\log_m2-\\log_m3=2$，故 $m^{-2}=6$，解得 $m=\\sqrt6/6$。",
}


def segment_questions(records: list[dict[str, object]]) -> dict[int, list[str]]:
    starts: list[tuple[int, int]] = []
    for pos, record in enumerate(records):
        match = re.match(r"^(\d+)．", str(record["text"]))
        if match:
            starts.append((pos, int(match.group(1))))
    result: dict[int, list[str]] = {}
    for index, (start, number) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(records)
        result[number] = [str(records[pos]["text"]).strip() for pos in range(start, end) if str(records[pos]["text"]).strip()]
    return result


def parse_question(number: int, lines: list[str]) -> dict[str, object]:
    lines = [line for line in lines if not re.match(r"^[一二三四]、(?:单选题|填空题|解答题)$", line)]
    answer_index = next(i for i, line in enumerate(lines) if line.startswith("【答案】"))
    difficulty_index = next(i for i, line in enumerate(lines) if line.startswith("【难度】"))
    solution_start = next(
        (i for i, line in enumerate(lines[difficulty_index + 1 :], difficulty_index + 1) if line.startswith(("【分析】", "【解析】", "【详解】"))),
        difficulty_index + 1,
    )
    pre_answer = lines[:answer_index]
    options, stem_lines = split_options(pre_answer)
    stem_lines[0] = re.sub(r"^\d+．", "", stem_lines[0]).strip()
    stem = "\n".join(stem_lines)
    answer_lines = [lines[answer_index].removeprefix("【答案】").strip(), *lines[answer_index + 1 : difficulty_index]]
    source_answer = "\n".join(line for line in answer_lines if line)
    solution_lines = [*answer_lines, *lines[solution_start:]]
    solution = "\n".join(line for line in solution_lines if line)
    record: dict[str, object] = {
        "id": QUESTION_IDS[number],
        "source_question_no": number,
        "stem": clean_latex(STEM_OVERRIDES.get(number, stem)),
        "answer": ANSWER_OVERRIDES[number],
        "solution": clean_latex(solution),
        "source_answer": clean_latex(source_answer),
    }
    if options:
        record["options"] = [clean_latex(option) for option in options]
    return record


def main() -> None:
    records = json.loads((IMPORT_DIR / "omml_extract.json").read_text(encoding="utf-8"))
    segments = segment_questions(records)
    if sorted(segments) != list(range(1, 22)):
        raise SystemExit(f"expected questions 1..21, got {sorted(segments)}")
    questions = [parse_question(number, segments[number]) for number in range(1, 22)]
    payload = {
        "source": {
            "name": "北京市东直门中学2025-2026学年高二下学期期末考试数学试题",
            "file": str(IMPORT_DIR / "exam.docx"),
            "sha256": "E4BF5EF55622A516C81A94D10DA646994B5CDFAF5BC9537A2FFF1BB1957DAABB",
            "formula_format": "Microsoft Office Math (OMML)",
        },
        "questions": questions,
    }
    correction_path = AUDIT_DIR / "2026-07-18-dongzhimen-omml-corrections.json"
    correction_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    audit_lines = [
        "# 东直门中学 2025-2026 高二下数学试卷逐题验证",
        "",
        "- 原文件 SHA-256：`E4BF5EF55622A516C81A94D10DA646994B5CDFAF5BC9537A2FFF1BB1957DAABB`",
        "- 公式来源：Microsoft Office Math（OMML），未使用普通文本 OCR。",
        "- 处理原则：逐题核对题干、选项、答案、解析、知识点、难度与重复项；数据库提升状态仅使用 `annotate --verify`。",
        "",
        "## 逐题复核",
        "",
    ]
    for number in range(1, 22):
        question_id = QUESTION_IDS[number]
        difficulty, qtype, knowledge, causes = META[number]
        audit_lines.extend(
            [
                f"### 第 {number} 题（`{question_id}`）",
                "",
                f"- 独立核算：{REVIEW_NOTES[number]}",
                f"- 结论：答案 `{ANSWER_OVERRIDES[number]}`；难度 `{difficulty}`；题型 `{qtype}`。",
                f"- 知识点：`{'`, `'.join(knowledge)}`；训练目标：`{'`, `'.join(causes)}`。",
                "",
            ]
        )
    audit_path = AUDIT_DIR / "2026-07-18-dongzhimen-omml-verification.md"
    audit_path.write_text("\n".join(audit_lines), encoding="utf-8")
    print(json.dumps({"questions": len(questions), "corrections": str(correction_path), "audit": str(audit_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
