"""Convert a site-style OMML DOCX extraction into unverified import JSONL.

The converter preserves source answers and explanations but never promotes their
verification state.  Promotion remains an item-level notebook.py operation.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from docx_parsing import clean_latex, split_options


QUESTION_START_RE = re.compile(
    r"^\s*(?:第\s*)?(\d{1,2})\s*(?:．|\.(?!\d)|、|题(?:\s*[:：])?)"
)
SECTION_RE = re.compile(r"^[一二三四五六七八九十]+、")
SECTION_COUNT_RE = re.compile(r"(?:本题)?共\s*(\d{1,2})\s*(?:小题|题)")
FIELD_MARKER_RE = re.compile(
    r"(?=【(?:答案|难度|知识点|分析|解析|详解|解答)】)"
)
SOLUTION_MARKERS = ("【分析】", "【解析】", "【详解】", "【解答】")
METADATA_MARKERS = ("【难度】", "【知识点】")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


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


def normalize_field_lines(lines: list[str]) -> list[str]:
    """Put site metadata markers on separate logical lines."""
    normalized: list[str] = []
    for line in lines:
        parts = [part.strip() for part in FIELD_MARKER_RE.split(line) if part.strip()]
        normalized.extend(parts)
    return normalized


def analyze_segments(
    records: list[dict[str, object]],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Split questions and retain enough provenance to detect silent losses."""
    starts: list[tuple[int, int, str]] = []
    section = ""
    in_numbered_tip_block = False
    declared_section_counts: list[int] = []
    for position, record in enumerate(records):
        text = str(record["text"]).strip()
        if SECTION_RE.match(text):
            section = text
            count_match = SECTION_COUNT_RE.search(text)
            if count_match:
                declared_section_counts.append(int(count_match.group(1)))
        if text.startswith("【点睛】"):
            in_numbered_tip_block = True
        match = QUESTION_START_RE.match(text)
        if match:
            number = int(match.group(1))
            if re.fullmatch(r"\d{1,2}．\d+(?:\.\d+)?", text):
                continue
            # Numbered method tips such as “1、… 2、… 3、…” often appear
            # after a question's 点睛 paragraph. Within the same section,
            # real exam question numbers advance monotonically; ignore only
            # non-increasing lookalikes and still accept the next real number.
            if (
                in_numbered_tip_block
                and starts
                and number <= starts[-1][1]
                and section == starts[-1][2]
            ):
                continue
            starts.append((position, number, section))
            in_numbered_tip_block = False

    segments: list[dict[str, object]] = []
    for index, (start, number, current_section) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(records)
        lines = [
            str(records[pos]["text"]).strip()
            for pos in range(start, end)
            if str(records[pos]["text"]).strip()
        ]
        lines = [line for line in lines if not SECTION_RE.match(line)]
        segments.append({
            "number": number,
            "section": current_section,
            "lines": normalize_field_lines(lines),
            "record_start": int(records[start].get("index", start)),
            "record_end": int(records[end - 1].get("index", end - 1)),
        })

    numbers = [number for _, number, _ in starts]
    duplicate_numbers = sorted(
        number for number in set(numbers) if numbers.count(number) > 1
    )
    number_gaps = (
        sorted(set(range(1, max(numbers) + 1)) - set(numbers)) if numbers else []
    )
    diagnostics: dict[str, object] = {
        "detected_question_numbers": numbers,
        "duplicate_question_numbers": duplicate_numbers,
        "missing_question_numbers": number_gaps,
        "leading_unassigned_records": starts[0][0] if starts else len(records),
        "detected_segments": len(segments),
        "declared_question_count": sum(declared_section_counts) or None,
    }
    return segments, diagnostics


def sectioned_segments(records: list[dict[str, object]]) -> list[tuple[int, str, list[str]]]:
    """Compatibility view used by older callers and tests."""
    segments, _ = analyze_segments(records)
    return [
        (int(segment["number"]), str(segment["section"]), list(segment["lines"]))
        for segment in segments
    ]


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
    lines = normalize_field_lines(lines)
    answer_index = next(
        (i for i, line in enumerate(lines) if line.startswith("【答案】")), None
    )
    if answer_index is None:
        raise ValueError("missing answer marker")
    solution_start = next(
        (
            i
            for i, line in enumerate(lines[answer_index + 1 :], answer_index + 1)
            if line.startswith(SOLUTION_MARKERS)
        ),
        None,
    )
    if solution_start is None:
        raise ValueError("missing solution marker")
    answer_end = next(
        (
            i
            for i, line in enumerate(lines[answer_index + 1 :], answer_index + 1)
            if line.startswith((*METADATA_MARKERS, *SOLUTION_MARKERS))
        ),
        solution_start,
    )
    if "选择" in section or "选题" in section:
        options, stem_lines = split_options(lines[:answer_index])
    else:
        options, stem_lines = [], list(lines[:answer_index])
    if not stem_lines:
        raise ValueError("empty stem after option splitting")
    stem_lines[0] = QUESTION_START_RE.sub("", stem_lines[0], count=1).strip()
    stem = localize_images(clean_latex("\n".join(stem_lines)), batch_name, relative_dir)
    answer_lines = [lines[answer_index].removeprefix("【答案】").strip(), *lines[answer_index + 1 : answer_end]]
    answer = localize_images(
        clean_latex("\n".join(line for line in answer_lines if line)),
        batch_name,
        relative_dir,
    )
    first_solution = lines[solution_start]
    for marker in SOLUTION_MARKERS:
        if first_solution.startswith(marker):
            first_solution = first_solution.removeprefix(marker).strip()
            break
    solution_lines = [first_solution, *lines[solution_start + 1 :]]
    solution = localize_images(clean_latex("\n".join(line for line in solution_lines if line)), batch_name, relative_dir)
    if len(stem) < 8:
        raise ValueError("stem too short")
    if not answer and ("选择" in section or "选题" in section):
        explicit_choices = re.findall(r"故选\s*[:：]\s*([A-D])", solution)
        if explicit_choices:
            answer = explicit_choices[-1]
    if not answer:
        raise ValueError("answer is empty")
    if len(solution) < 8:
        raise ValueError("solution body missing or too short")
    difficulty_index = next(
        (i for i, line in enumerate(lines) if line.startswith("【难度】")), None
    )
    knowledge_text = " ".join(
        line for line in lines[answer_end:solution_start] if line.startswith(METADATA_MARKERS)
    )
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
        "source_section": section,
    }
    if options:
        record["options"] = [
            localize_images(clean_latex(option), batch_name, relative_dir)
            for option in options
        ]
    return record


def build_quality_problems(
    diagnostics: dict[str, object],
    questions: list[dict[str, object]],
    skipped: list[dict[str, object]],
    image_root: Path | None = None,
) -> list[str]:
    problems: list[str] = []
    if not diagnostics.get("detected_segments"):
        problems.append("no_question_boundaries_detected")
    if diagnostics.get("duplicate_question_numbers"):
        problems.append("duplicate_question_numbers")
    if diagnostics.get("missing_question_numbers"):
        problems.append("missing_question_numbers")
    if skipped:
        problems.append("question_parse_failures")
    if len(questions) != int(diagnostics.get("detected_segments") or 0):
        problems.append("parsed_question_count_mismatch")
    declared_count = diagnostics.get("declared_question_count")
    if declared_count and int(declared_count) != int(diagnostics.get("detected_segments") or 0):
        problems.append("declared_question_count_mismatch")
    for question in questions:
        qid = str(question.get("id") or "unknown")
        for field in ("stem", "answer", "solution"):
            value = str(question.get(field) or "")
            if not value.strip():
                problems.append(f"{qid}:missing_{field}")
            if value.count("$") % 2:
                problems.append(f"{qid}:unbalanced_math_delimiter:{field}")
        options = question.get("options")
        if "选择" in str(question.get("source_section") or "") and not options:
            problems.append(f"{qid}:missing_choice_options")
        if options:
            labels = [str(option).split("．", 1)[0] for option in options]
            if labels != ["A", "B", "C", "D"]:
                problems.append(f"{qid}:invalid_choice_labels")
        image_text = "\n".join([
            str(question.get("stem") or ""),
            str(question.get("answer") or ""),
            str(question.get("solution") or ""),
            "\n".join(str(option) for option in (options or [])),
        ])
        if "[IMAGE:" in image_text:
            problems.append(f"{qid}:unlocalized_image_marker")
        if image_root is not None:
            for reference in IMAGE_RE.findall(image_text):
                path_text = reference.strip().strip("<>").split(maxsplit=1)[0]
                image_path = image_root / Path(path_text).name
                if not image_path.is_file():
                    problems.append(f"{qid}:missing_image:{path_text}")
    return sorted(set(problems))


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
    questions: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    segments, diagnostics = analyze_segments(records)
    for segment in segments:
        number = int(segment["number"])
        section = str(segment["section"])
        lines = list(segment["lines"])
        try:
            q = parse_question(number, section, lines, args.relative_dir, args.batch_name, args.grade, args.semester, args.source_year)
            q["source_paragraph_start"] = segment["record_start"]
            q["source_paragraph_end"] = segment["record_end"]
            questions.append(q)
        except (IndexError, StopIteration, TypeError, ValueError, KeyError) as exc:
            skipped.append({
                "question_number": number,
                "record_start": segment["record_start"],
                "record_end": segment["record_end"],
                "reason": str(exc) or type(exc).__name__,
            })
    output = args.exam_dir / "questions.jsonl"
    output.write_text("".join(json.dumps(question, ensure_ascii=False) + "\n" for question in questions), encoding="utf-8")
    quality_problems = build_quality_problems(
        diagnostics, questions, skipped, args.exam_dir / "media"
    )
    summary = {
        "questions": len(questions),
        "with_options": sum("options" in question for question in questions),
        "with_answers": sum(bool(question["answer"]) for question in questions),
        "with_solutions": sum(bool(question["solution"]) for question in questions),
        **diagnostics,
        "skipped_questions": skipped,
        "quality_gate": {
            "passed": not quality_problems,
            "problems": quality_problems,
        },
        "output": str(output),
    }
    (args.exam_dir / "parse_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
