"""Convert a site-style OMML DOCX extraction into unverified import JSONL.

The converter preserves source answers and explanations but never promotes their
verification state.  Promotion remains an item-level notebook.py operation.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from build_dongzhimen_review import clean_latex, split_options


def infer_knowledge(text: str) -> list[str]:
    rules = [
        ("complex-numbers", ("复数", "共轭")),
        ("plane-vectors", ("平面向量", "向量", "数量积")),
        ("solid-geometry", ("立体", "空间", "线面", "面面", "棱锥", "棱柱", "球")),
        ("probability", ("概率", "独立事件", "古典概型")),
        ("statistics", ("统计", "抽样", "平均数", "方差", "百分位")),
        ("sets", ("集合", "交集", "并集")),
        ("logic", ("充分", "必要", "命题", "量词")),
        ("inequalities", ("不等式", "基本不等式")),
        ("exponential-log", ("指数", "对数", "幂函数")),
        ("trig-graphs", ("三角函数图象", "周期", "单调区间", "最小正周期")),
        ("trig-identities", ("三角恒等", "诱导公式", "和差", "二倍角", "辅助角")),
        ("trig-definition", ("任意角", "终边", "三角函数值", "同角")),
        ("sine-cosine-laws", ("正弦定理", "余弦定理", "解三角形", "三角形面积")),
        ("function-properties", ("函数", "奇偶", "单调", "定义域", "值域")),
        ("conic-ellipse", ("椭圆", "离心率", "焦点")),
        ("conic-hyperbola", ("双曲线", "渐近线")),
        ("conic-parabola", ("抛物线", "准线")),
        ("space-vectors", ("空间向量", "法向量", "方向向量")),
        ("derivatives", ("导数", "切线", "极值", "单调性")),
        ("derivative-applications", ("导数的应用", "恒成立", "存在性")),
        ("arithmetic-sequences", ("等差数列", "公差")),
        ("geometric-sequences", ("等比数列", "公比")),
        ("line-circle", ("直线", "圆", "斜率")),
        ("sequences", ("数列", "递推", "前n项")),
        ("counting-binomial", ("排列", "组合", "二项式", "计数原理")),
    ]
    # Rule order matters: conic-ellipse must match before line-circle (圆 in 椭圆)
    # line-circle: match standalone 圆 (not preceded by 椭/圆柱/圆锥) or 直线
    codes = []
    for code, words in rules:
        if code == "line-circle":
            # Only match 圆 when it is not part of 椭圆/圆柱/圆锥
            if "直线" in text:
                codes.append(code)
            elif "圆" in text and "椭圆" not in text and "圆柱" not in text and "圆锥" not in text:
                codes.append(code)
        else:
            if any(word in text for word in words):
                codes.append(code)
    return codes or ["algebra-operations"]


def infer_targets(knowledge: list[str]) -> list[str]:
    targets = ["knowledge_gap", "method_choice"]
    if any(code in knowledge for code in ("complex-numbers", "trig-identities", "probability", "plane-vectors")):
        targets.append("calculation")
    return targets


def convert_difficulty(value: float) -> float:
    if value >= 0.85:
        return 2.0
    if value >= 0.70:
        return 2.5
    if value >= 0.55:
        return 3.0
    if value >= 0.40:
        return 3.5
    if value >= 0.25:
        return 4.0
    return 4.5


def question_type(section: str, options: list[str]) -> str:
    if "多选" in section:
        return "多选题"
    if options:
        return "单选题"
    if "填空" in section:
        return "填空题"
    return "解答题"


def sectioned_segments(records: list[dict[str, object]]) -> list[tuple[int, str, list[str]]]:
    starts: list[tuple[int, int, str]] = []
    section = ""
    for position, record in enumerate(records):
        text = str(record["text"]).strip()
        if re.match(r"^[一二三四五六]、", text):
            section = text
        match = re.match(r"^(\d+)．", text)
        if match:
            starts.append((position, int(match.group(1)), section))
    result = []
    for index, (start, number, current_section) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(records)
        lines = [str(records[pos]["text"]).strip() for pos in range(start, end) if str(records[pos]["text"]).strip()]
        lines = [line for line in lines if not re.match(r"^[一二三四五六]、", line)]
        result.append((number, current_section, lines))
    return result


def localize_images(value: str, batch_name: str, relative_dir: str) -> str:
    return re.sub(
        r"\[IMAGE:media/([^\]]+)\]",
        lambda match: f"![原题图](data/imports/{batch_name}/{relative_dir}/media/{match.group(1)})",
        value,
    )


def parse_question(
    number: int,
    section: str,
    lines: list[str],
    relative_dir: str,
    batch_name: str,
    grade: int,
    semester: int,
    source_year: str,
) -> dict[str, object]:
    answer_index = next(i for i, line in enumerate(lines) if line.startswith("【答案】"))
    difficulty_index = next((i for i, line in enumerate(lines) if line.startswith("【难度】")), None)
    answer_end = difficulty_index if difficulty_index is not None else next(
        (i for i, line in enumerate(lines[answer_index + 1 :], answer_index + 1) if line.startswith(("【知识点】", "【分析】", "【解析】", "【详解】"))),
        len(lines),
    )
    solution_start = next(
        (i for i, line in enumerate(lines[answer_end:], answer_end) if line.startswith(("【分析】", "【解析】", "【详解】"))),
        answer_end,
    )
    options, stem_lines = split_options(lines[:answer_index])
    stem_lines[0] = re.sub(r"^\d+．", "", stem_lines[0]).strip()
    stem = localize_images(clean_latex("\n".join(stem_lines)), batch_name, relative_dir)
    answer_lines = [lines[answer_index].removeprefix("【答案】").strip(), *lines[answer_index + 1 : answer_end]]
    answer = clean_latex("\n".join(line for line in answer_lines if line))
    solution_lines = [*answer_lines, *lines[solution_start:]]
    solution = localize_images(clean_latex("\n".join(line for line in solution_lines if line)), batch_name, relative_dir)
    metadata_start = (difficulty_index + 1) if difficulty_index is not None else answer_end
    knowledge_text = " ".join(lines[metadata_start:solution_start])
    knowledge = infer_knowledge(knowledge_text + " " + stem)
    difficulty_match = re.search(r"([0-9.]+)", lines[difficulty_index]) if difficulty_index is not None else None
    difficulty = convert_difficulty(float(difficulty_match.group(1))) if difficulty_match else 3.0
    record: dict[str, object] = {
        "id": f"{relative_dir.upper()}-Q{number:02d}",
        "stem": stem,
        "answer": answer,
        "solution": solution,
        "grade": grade,
        "semester": semester,
        "question_type": question_type(section, options),
        "difficulty": difficulty,
        "knowledge_codes": knowledge,
        "target_causes": infer_targets(knowledge),
        "source_year": source_year,
        "source_question_no": number,
        "verified": False,
    }
    if options:
        record["options"] = [clean_latex(option) for option in options]
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("exam_dir", type=Path)
    parser.add_argument("--relative-dir", required=True)
    parser.add_argument("--batch-name", default="2026-07-18-g10-final-5")
    parser.add_argument("--grade", type=int, choices=(10, 11, 12), default=10)
    parser.add_argument("--semester", type=int, choices=(1, 2), default=2)
    parser.add_argument("--source-year", default="2025-2026")
    args = parser.parse_args()
    records = json.loads((args.exam_dir / "omml_extract.json").read_text(encoding="utf-8"))
    questions = []
    skipped = 0
    for number, section, lines in sectioned_segments(records):
        try:
            q = parse_question(number, section, lines, args.relative_dir, args.batch_name, args.grade, args.semester, args.source_year)
            questions.append(q)
        except (StopIteration, ValueError, KeyError) as e:
            import sys
            print(f'Skipping Q{number} ({section[:30]}): {e}', file=sys.stderr)
            skipped += 1
    if skipped:
        print(f'Skipped {skipped} non-question entries', file=sys.stderr)
    output = args.exam_dir / "questions.jsonl"
    output.write_text("".join(json.dumps(question, ensure_ascii=False) + "\n" for question in questions), encoding="utf-8")
    summary = {
        "questions": len(questions),
        "with_options": sum("options" in question for question in questions),
        "with_answers": sum(bool(question["answer"]) for question in questions),
        "with_solutions": sum(bool(question["solution"]) for question in questions),
        "output": str(output),
    }
    (args.exam_dir / "parse_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
