#!/usr/bin/env python3
"""Create and optionally print an A4 practice PDF from saved recommendations."""

from __future__ import annotations

import argparse
import hashlib
import html
import itertools
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = SKILL_DIR.parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "math_notebook.db"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "math-error-notebook.json"


def bundled_python() -> Path | None:
    candidate = (
        Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime"
        / "dependencies" / "python" / "python.exe"
    )
    return candidate if candidate.is_file() else None


def ensure_reportlab() -> None:
    try:
        import reportlab  # noqa: F401
    except ModuleNotFoundError:
        python = bundled_python()
        if not python or Path(sys.executable).resolve() == python.resolve():
            raise RuntimeError("reportlab is unavailable and bundled Python was not found")
        completed = subprocess.run([str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
        raise SystemExit(completed.returncode)


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_items(
    db_path: Path, error_id: str
) -> tuple[sqlite3.Row, list[sqlite3.Row], list[str]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    error = conn.execute("SELECT * FROM errors WHERE id=?", (error_id,)).fetchone()
    if not error:
        conn.close()
        raise ValueError(f"error not found: {error_id}")
    rows = conn.execute(
        """SELECT r.rank,r.reason,q.id AS question_id,q.stem,q.options_json,q.answer,
                  q.solution,q.difficulty,q.source_name
           FROM recommendations r JOIN questions q ON q.id=r.question_id
           WHERE r.error_id=? AND q.verified=1 ORDER BY r.rank""",
        (error_id,),
    ).fetchall()
    knowledge_names = [
        item[0]
        for item in conn.execute(
            """SELECT kp.name FROM error_knowledge ek
               JOIN knowledge_points kp ON kp.code = ek.knowledge_code
               WHERE ek.error_id=? ORDER BY kp.name""",
            (error_id,),
        )
    ]
    conn.close()
    if not rows:
        raise ValueError("no saved verified recommendations for this error")
    return error, rows, knowledge_names


def _cause_name(cause_code: str | None) -> str:
    """Resolve a cause code to its display name, reusing notebook.py's taxonomy."""
    if not cause_code:
        return ""
    try:
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        import notebook
        return notebook.CAUSE_CODES.get(cause_code, cause_code)
    except Exception:
        return cause_code


MATH_CACHE_DIR = PROJECT_ROOT / "output" / "pdf" / "math"
_MATH_REGISTRY: dict[str, str] = {}
_MATH_COUNTER = itertools.count()
_HAS_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]")


def _register_math(latex: str) -> str:
    token = f"ZZMATH{next(_MATH_COUNTER):04d}ZZ"
    _MATH_REGISTRY[token] = latex
    return token


def _extract_math(text: str) -> str:
    """Replace ``$...$`` segments and bare ``\\sqrt{...}`` with unique placeholders.

    Placeholders survive ``_latex_to_text`` and ``html.escape`` untouched and are
    later swapped for inline mathtext images inside ``paragraph_text``, so all
    mathematical notation is typeset (fractions, radicals, scripts, symbols)
    instead of plain-text approximations like "(1)/(2)" or "√(x)".
    """
    out: list[str] = []
    i = 0
    while True:
        j = text.find("$", i)
        if j < 0:
            out.append(text[i:])
            break
        k = text.find("$", j + 1)
        if k < 0:  # unpaired dollar: keep the remainder literal
            out.append(text[i:])
            break
        out.append(text[i:j])
        latex = text[j + 1:k]
        out.append(_register_math(latex) if latex.strip() else "")
        i = k + 1
    value = "".join(out)

    token = "\\sqrt{"
    out = []
    i = 0
    while True:
        j = value.find(token, i)
        if j < 0:
            out.append(value[i:])
            break
        out.append(value[i:j])
        depth = 0
        k = j + len(token) - 1  # position of the opening brace
        start = k + 1
        while k < len(value):
            ch = value[k]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if k >= len(value):  # unbalanced braces: keep the remainder literal
            out.append(value[j:])
            break
        out.append(_register_math(f"\\sqrt{{{value[start:k]}}}"))
        i = k + 1
    return "".join(out)


def _render_math_image(latex: str, font_size: float) -> tuple[str, float, float]:
    """Render one math segment with matplotlib mathtext; return (url, w_pt, h_pt)."""
    key = hashlib.sha256(f"{latex}|{font_size:.2f}".encode("utf-8")).hexdigest()[:16]
    MATH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = MATH_CACHE_DIR / f"math-{key}.png"
    if not out.is_file():
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        fig = plt.figure(figsize=(0.01, 0.01))
        fig.text(0, 0, f"${latex}$", fontsize=font_size)
        fig.savefig(out, dpi=300, bbox_inches="tight", pad_inches=0.012, transparent=True)
        plt.close(fig)
    from PIL import Image
    with Image.open(out) as im:
        w_px, h_px = im.size
    url = out.resolve().as_posix()
    return url, w_px * 72.0 / 300.0, h_px * 72.0 / 300.0


def _math_img_tag(placeholder: str, font_size: float) -> str:
    latex = _MATH_REGISTRY.get(placeholder)
    if latex is None:
        return placeholder
    # Segments mathtext cannot handle (CJK text, unsupported constructs) fall
    # back to the plain-text conversion for that segment only.
    if _HAS_CJK.search(latex):
        return html.escape(_latex_to_text(latex))
    try:
        url, w_pt, h_pt = _render_math_image(latex, font_size)
    except Exception:
        return html.escape(_latex_to_text(latex))
    # Drop the image slightly below the text baseline so it sits naturally.
    valign = -(h_pt * 0.22)
    return (
        f'<img src="{url}" width="{w_pt:.1f}" height="{h_pt:.1f}" '
        f'valign="{valign:.1f}"/>'
    )


def clean_math(text: str | None) -> str:
    return _latex_to_text(_extract_math(text or ""))


def _latex_to_text(value: str) -> str:
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "[题图见原题]", value)
    value = value.replace("\\left", "").replace("\\right", "")
    value = re.sub(r"\^\{2\}", "²", value)
    value = re.sub(r"\^\{3\}", "³", value)
    value = re.sub(r"\^\{([^{}]+)\}", r"^(\1)", value)
    value = re.sub(r"_\{([^{}]+)\}", r"_(\1)", value)
    value = re.sub(r"\\overrightarrow\{([^{}]+)\}", r"vec(\1)", value)
    value = re.sub(r"\\vec\{([^{}]+)\}", r"vec(\1)", value)
    value = re.sub(r"\\(?:text|mathrm|operatorname)\{([^{}]+)\}", r"\1", value)
    value = re.sub(r"\\frac\s*([0-9A-Za-z])\s*([0-9A-Za-z])", r"(\1)/(\2)", value)

    def _fmt_sqrt(match: "re.Match[str]") -> str:
        inner = match.group(1)
        # 书面通用格式：纯数字/字母的简单被开方数不带括号（√41、√2）；
        # 复合被开方数保留括号标示根号覆盖范围（√(x+1)）。
        if re.fullmatch(r"[0-9A-Za-z]+", inner):
            return "√" + inner
        return "√(" + inner + ")"

    for _ in range(8):
        before = value
        value = re.sub(r"\\sqrt\{([^{}]+)\}", _fmt_sqrt, value)
        value = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", value)
        if value == before:
            break
    replacements = {
        "\\le": "≤", "\\ge": "≥", "\\ne": "≠", "\\perp": "⊥",
        "\\parallel": "∥", "\\pi": "π", "\\infty": "∞",
        "\\cdot": "·", "\\times": "×", "\\pm": "±",
        "\\angle": "∠", "\\triangle": "△", "\\therefore": "∴",
        "\\because": "∵", "\\in": "∈", "\\notin": "∉",
        "\\subseteq": "⊆", "\\cup": "∪", "\\cap": "∩",
        "\\Rightarrow": "⇒", "\\Leftarrow": "⇐", "\\Leftrightarrow": "⇔",
        "\\rightarrow": "→", "\\leftarrow": "←",
        "\\begin{aligned}": "", "\\end{aligned}": "",
        "\\begin{cases}": "", "\\end{cases}": "",
        "\\quad": "  ", "\\;": " ", "\\,": " ",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = value.replace("\\\\", "\n")
    value = re.sub(r"\\(sin|cos|tan|cot|log|ln|max|min)\b", r"\1", value)
    value = re.sub(r"\\([A-Za-z]+)", r"\1", value)
    value = value.replace("$", "").replace("`", "").replace("{", "").replace("}", "")
    value = value.replace("^2", "²").replace("^3", "³")
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


_STEM_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]*)\)")


def split_stem_images(text: str | None) -> tuple[str, list[str]]:
    """Strip embedded ``![..](path)`` refs; return (text_without_refs, [paths]).

    ``_latex_to_text`` would otherwise replace them with the "[题图见原题]"
    placeholder, which leaves students without the diagram. The caller renders
    the referenced images as block figures right after the paragraph instead.
    """
    paths: list[str] = []

    def _sub(match: "re.Match[str]") -> str:
        paths.append(match.group(1).strip())
        return ""

    cleaned = _STEM_IMAGE_RE.sub(_sub, text or "")
    return cleaned, paths


def concise_solution(text: str | None) -> str:
    value = text or ""
    if "完整解答" in value:
        value = value.split("完整解答", 1)[1]
    return clean_math(value)


def paragraph_text(text: str | None, font_size: float = 11.0) -> str:
    escaped = html.escape(clean_math(text)).replace("\n", "<br/>")
    return re.sub(
        r"ZZMATH\d{4}ZZ",
        lambda match: _math_img_tag(match.group(0), font_size),
        escaped,
    )


def find_soffice() -> Path | None:
    candidates = (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    )
    return next((item for item in candidates if item.is_file()), None)


def print_pdf(pdf: Path, printer: str | None) -> None:
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice was not found; PDF was created but not printed")
    command = [str(soffice), "--headless"]
    command.extend(["--pt", printer] if printer else ["-p"])
    command.append(str(pdf.resolve()))
    subprocess.run(command, check=True, timeout=60)


def create_pdf(
    output: Path,
    error_id: str,
    error: sqlite3.Row,
    items: list[sqlite3.Row],
    solution_chars: int,
    include_answers: bool = False,
    knowledge_names: list[str] | None = None,
) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import CondPageBreak, HRFlowable, Image as RLImage, PageBreak, Paragraph, SimpleDocTemplate, Spacer

    regular = Path(r"C:\Windows\Fonts\msyh.ttc")
    bold = Path(r"C:\Windows\Fonts\msyhbd.ttc")
    try:
        pdfmetrics.registerFont(TTFont("PracticeCN", str(regular), subfontIndex=0))
        pdfmetrics.registerFont(TTFont("PracticeCN-Bold", str(bold), subfontIndex=0))
        font, font_bold = "PracticeCN", "PracticeCN-Bold"
    except Exception:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        font = font_bold = "STSong-Light"

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "title-cn", parent=styles["Title"], fontName=font_bold, fontSize=18,
        leading=24, alignment=TA_CENTER, textColor=colors.HexColor("#173B57"),
        spaceAfter=7 * mm,
    )
    heading = ParagraphStyle(
        "heading-cn", parent=styles["Heading2"], fontName=font_bold, fontSize=13,
        leading=19, textColor=colors.HexColor("#175CD3"), spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )
    body = ParagraphStyle(
        "body-cn", parent=styles["Normal"], fontName=font, fontSize=11,
        leading=18, textColor=colors.HexColor("#101828"), spaceAfter=3 * mm,
    )
    meta = ParagraphStyle(
        "meta-cn", parent=body, fontSize=8.5, leading=13,
        textColor=colors.HexColor("#667085"), spaceAfter=4 * mm,
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(18 * mm, 12 * mm, f"高中数学错题本 · {error_id}")
        canvas.drawRightString(192 * mm, 12 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    # 题图：相对路径相对项目根目录解析，限制在版心内等比缩放。
    max_img_w = 150 * mm
    max_img_h = 78 * mm

    def image_flowable(rel_path: str):
        path = Path(rel_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / rel_path
        if not path.is_file():
            return None
        try:
            from PIL import Image as PILImage
            with PILImage.open(path) as im:
                w_px, h_px = im.size
        except Exception:
            return None
        if w_px <= 0 or h_px <= 0:
            return None
        scale = min(max_img_w / w_px, max_img_h / h_px, 2.0)
        return RLImage(str(path), width=w_px * scale, height=h_px * scale)

    def text_flowables(text: str | None, style, font_size: float = 11.0, prefix: str = ""):
        cleaned, img_paths = split_stem_images(text)
        flows = [Paragraph(prefix + paragraph_text(cleaned, font_size), style)]
        for rel in img_paths:
            flowable = image_flowable(rel)
            flows.append(flowable if flowable is not None else Paragraph("［题图缺失，见原卷］", meta))
        return flows

    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=17 * mm, bottomMargin=19 * mm, title=f"针对性练习 {error_id}",
        author="高中数学错题本",
    )
    knowledge_names = knowledge_names or []
    info_bits: list[str] = []
    if knowledge_names:
        info_bits.append("知识点：" + "、".join(html.escape(name) for name in knowledge_names))
    cause_label = _cause_name(error["cause_code"])
    if cause_label:
        info_bits.append(f"错因：{html.escape(cause_label)}")
    story = [Paragraph("错因针对性练习", title)]
    story.extend(text_flowables(error["problem_text"], body, prefix="<b>错题原题：</b>"))
    story.extend([
        Paragraph("　　".join(info_bits) if info_bits else "知识点：—", body),
        Paragraph(
            "姓名：____________　日期：____________　先独立完成，全部做完后拍照发给我判。",
            body,
        ),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#84ADFF")),
    ])
    for row in items:
        if row["rank"] > 1:
            story.append(CondPageBreak(80 * mm))
        story.append(
            Paragraph(f"{row['rank']}　题库编号 {row['question_id']}（难度 {row['difficulty']}/5）", heading)
        )
        story.extend(text_flowables(row["stem"], body))
        if row["options_json"]:
            options = json.loads(row["options_json"])
            story.append(Paragraph("<br/>".join(paragraph_text(item) for item in options), body))
        story.extend([
            Paragraph(f"推荐理由：{paragraph_text(row['reason'], 8.5)}<br/>来源：{paragraph_text(row['source_name'], 8.5)}", meta),
            Spacer(1, 22 * mm),
        ])

    if include_answers:
        story.extend([PageBreak(), Paragraph("答案与解析", title)])
        for row in items:
            if row["rank"] > 1:
                story.append(CondPageBreak(100 * mm))
            solution_stripped, solution_images = split_stem_images(row["solution"])
            solution = concise_solution(solution_stripped)
            if len(solution) > solution_chars:
                solution = solution[:solution_chars].rstrip() + "……（完整解析保存在题库中）"
            story.extend([
                Paragraph(f"{row['rank']}　{row['question_id']}", heading),
                Paragraph(f"<b>答案：</b>{paragraph_text(row['answer'])}", body),
                Paragraph(paragraph_text(solution) or "题库暂无解析。", body),
            ])
            for rel in solution_images:
                flowable = image_flowable(rel)
                story.append(flowable if flowable is not None else Paragraph("［题图缺失，见原卷］", meta))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a compact A4 practice PDF")
    parser.add_argument("error_id")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--print", dest="do_print", action="store_true")
    parser.add_argument("--printer")
    parser.add_argument("--solution-chars", type=int, default=1200)
    parser.add_argument(
        "--with-answers",
        dest="with_answers",
        action="store_true",
        help="生成的 PDF 附答案页（默认不附答案：孩子做完后拍照判题）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_reportlab()
    config = load_config(args.config)
    output = args.output or (PROJECT_ROOT / "output" / "pdf" / f"{args.error_id}-practice.pdf")
    error, items, knowledge_names = load_items(args.db, args.error_id)
    # 默认不附答案页（孩子做完后拍照判题）；--with-answers 或配置项可显式打开。
    include_answers = bool(args.with_answers or config.get("answers_after_questions", False))
    create_pdf(
        output, args.error_id, error, items, max(300, args.solution_chars),
        include_answers=include_answers,
        knowledge_names=knowledge_names,
    )
    printer = args.printer or config.get("printer_name")
    if args.do_print:
        if include_answers:
            # PDF 附答案页时，打印只打题目页：生成临时纯题目版送打印机后删除。
            questions_only = output.with_name(output.stem + "-questions-only.pdf")
            create_pdf(
                questions_only, args.error_id, error, items, max(300, args.solution_chars),
                include_answers=False,
                knowledge_names=knowledge_names,
            )
            try:
                print_pdf(questions_only, printer)
            finally:
                questions_only.unlink(missing_ok=True)
        else:
            print_pdf(output, printer)
    print(json.dumps({
        "pdf": str(output.resolve()),
        "questions": len(items),
        "with_answers": include_answers,
        "printed": bool(args.do_print),
        "print_scope": ("questions-only" if include_answers else "all") if args.do_print else None,
        "printer": printer if args.do_print else None,
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
