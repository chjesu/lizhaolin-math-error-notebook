#!/usr/bin/env python3
"""Create and optionally print an A4 practice PDF from saved recommendations."""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import itertools
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = SKILL_DIR.parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "math_notebook.db"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "math-error-notebook.json"
LOCAL_PDF_RUNTIME = PROJECT_ROOT / "runtime" / "pdf"
PDF_REQUIREMENTS = PROJECT_ROOT / "requirements-pdf.txt"
MPL_CONFIG_DIR = PROJECT_ROOT / "tmp" / "pdfs" / "matplotlib"

if LOCAL_PDF_RUNTIME.is_dir() and str(LOCAL_PDF_RUNTIME) not in sys.path:
    sys.path.insert(0, str(LOCAL_PDF_RUNTIME))
MPL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPL_CONFIG_DIR))


def bundled_python() -> Path | None:
    candidate = (
        Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime"
        / "dependencies" / "python" / "python.exe"
    )
    return candidate if candidate.is_file() else None


def missing_pdf_modules() -> list[str]:
    required = {"reportlab": "reportlab", "Pillow": "PIL", "matplotlib": "matplotlib"}
    return [name for name, module in required.items() if importlib.util.find_spec(module) is None]


def ensure_pdf_runtime() -> None:
    missing = missing_pdf_modules()
    if not missing:
        return
    python = bundled_python()
    if python and Path(sys.executable).resolve() != python.resolve():
        completed = subprocess.run([str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
        raise SystemExit(completed.returncode)
    install = (
        f'"{sys.executable}" -m pip install --target "{LOCAL_PDF_RUNTIME}" '
        f'-r "{PDF_REQUIREMENTS}"'
    )
    raise RuntimeError(
        "PDF runtime is incomplete (missing: " + ", ".join(missing) + "). "
        "Install the project dependencies once with: " + install
    )


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


MATH_CACHE_DIR = PROJECT_ROOT / "tmp" / "pdfs" / "math"
MATH_CACHE_VERSION = "v2"
_MATH_REGISTRY: dict[str, tuple[str, bool]] = {}
_MATH_COUNTER = itertools.count()
_HAS_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]")


def _register_math(latex: str, display: bool = False) -> str:
    token = f"ZZMATH{next(_MATH_COUNTER):04d}ZZ"
    _MATH_REGISTRY[token] = (latex, display)
    return token


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    index -= 1
    while index >= 0 and text[index] == "\\":
        backslashes += 1
        index -= 1
    return bool(backslashes % 2)


def _find_math_close(text: str, start: int, close: str) -> int:
    index = start
    while True:
        index = text.find(close, index)
        if index < 0:
            return -1
        if not _is_escaped(text, index):
            return index
        index += len(close)


def _extract_math(text: str) -> str:
    """Replace common LaTeX delimiters and bare ``\\sqrt{...}`` with placeholders.

    Placeholders survive ``_latex_to_text`` and ``html.escape`` untouched and are
    later swapped for inline mathtext images inside ``paragraph_text``, so all
    mathematical notation is typeset (fractions, radicals, scripts, symbols)
    instead of plain-text approximations like "(1)/(2)" or "√(x)".
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        if text.startswith(r"\$", i):
            out.append("ZZLITERALDOLLARZZ")
            i += 2
            continue
        opener = closer = ""
        display = False
        if text.startswith("$$", i):
            opener = closer = "$$"
            display = True
        elif text.startswith(r"\[", i):
            opener, closer, display = r"\[", r"\]", True
        elif text.startswith(r"\(", i):
            opener, closer = r"\(", r"\)"
        elif text[i] == "$" and not _is_escaped(text, i):
            opener = closer = "$"
        if not opener:
            out.append(text[i])
            i += 1
            continue
        k = _find_math_close(text, i + len(opener), closer)
        if k < 0:
            out.append(opener)
            i += len(opener)
            continue
        latex = text[i + len(opener):k]
        out.append(_register_math(latex, display) if latex.strip() else "")
        i = k + len(closer)
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
    import matplotlib
    key = hashlib.sha256(
        f"{MATH_CACHE_VERSION}|{matplotlib.__version__}|{latex}|{font_size:.2f}".encode("utf-8")
    ).hexdigest()[:16]
    MATH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = MATH_CACHE_DIR / f"math-{key}.png"
    if not out.is_file():
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        fig = plt.figure(figsize=(0.01, 0.01))
        fig.text(0, 0, "$" + latex + "$", fontsize=font_size)
        fig.savefig(out, dpi=360, bbox_inches="tight", pad_inches=0.012, transparent=True)
        plt.close(fig)
    from PIL import Image
    with Image.open(out) as im:
        w_px, h_px = im.size
    url = out.resolve().as_posix()
    return url, w_px * 72.0 / 360.0, h_px * 72.0 / 360.0


def _math_img_tag(placeholder: str, font_size: float) -> str:
    registered = _MATH_REGISTRY.get(placeholder)
    if registered is None:
        return placeholder
    latex, display = registered
    # Segments mathtext cannot handle (CJK text, unsupported constructs) fall
    # back to the plain-text conversion for that segment only.
    if _HAS_CJK.search(latex):
        return html.escape(_latex_to_text(latex))
    try:
        url, w_pt, h_pt = _render_math_image(latex, font_size)
    except Exception:
        return html.escape(_latex_to_text(latex))
    max_w = 455.0 if display else 435.0
    max_h = font_size * (2.15 if display else 1.45)
    scale = min(1.0, max_w / w_pt, max_h / h_pt)
    w_pt, h_pt = w_pt * scale, h_pt * scale
    valign = -(font_size * 0.18)
    tag = (
        f'<img src="{url}" width="{w_pt:.1f}" height="{h_pt:.1f}" '
        f'valign="{valign:.1f}"/>'
    )
    return f"<br/>{tag}<br/>" if display else tag


def clean_math(text: str | None) -> str:
    return _latex_to_text(_extract_math(text or "")).replace("ZZLITERALDOLLARZZ", "$")


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


def prepare_diagram_image(
    path: Path,
    temp_dir: Path,
    max_width_pt: float,
    max_height_pt: float,
) -> tuple[Path, float, float] | None:
    """Crop empty margins and return a DPI-aware, never-upscaled diagram size."""
    if not path.is_file():
        return None
    try:
        from PIL import Image, ImageChops

        with Image.open(path) as source:
            source.load()
            dpi_info = source.info.get("dpi", (144.0, 144.0))
            if not isinstance(dpi_info, (tuple, list)) or len(dpi_info) < 2:
                dpi_info = (144.0, 144.0)
            dpi_x, dpi_y = float(dpi_info[0]), float(dpi_info[1])
            if not 60.0 <= dpi_x <= 600.0:
                dpi_x = 144.0
            if not 60.0 <= dpi_y <= 600.0:
                dpi_y = 144.0

            rgba = source.convert("RGBA")
            canvas = Image.new("RGBA", rgba.size, "white")
            canvas.alpha_composite(rgba)
            rgb = canvas.convert("RGB")
            white = Image.new("RGB", rgb.size, "white")
            mask = ImageChops.difference(rgb, white).convert("L").point(
                lambda pixel: 255 if pixel > 12 else 0
            )
            bbox = mask.getbbox()
            if bbox:
                padding = 8
                left = max(0, bbox[0] - padding)
                top = max(0, bbox[1] - padding)
                right = min(rgb.width, bbox[2] + padding)
                bottom = min(rgb.height, bbox[3] + padding)
                rgb = rgb.crop((left, top, right, bottom))
            if rgb.width <= 0 or rgb.height <= 0:
                return None

            digest = hashlib.sha256(
                f"{path.resolve()}|{path.stat().st_mtime_ns}|diagram-v2".encode("utf-8")
            ).hexdigest()[:16]
            temp_dir.mkdir(parents=True, exist_ok=True)
            prepared = temp_dir / f"diagram-{digest}.png"
            rgb.save(prepared, format="PNG", dpi=(dpi_x, dpi_y), optimize=True)
    except Exception:
        return None

    natural_width = rgb.width * 72.0 / dpi_x
    natural_height = rgb.height * 72.0 / dpi_y
    scale = min(1.0, max_width_pt / natural_width, max_height_pt / natural_height)
    return prepared, natural_width * scale, natural_height * scale


def concise_solution(text: str | None) -> str:
    value = text or ""
    if "完整解答" in value:
        value = value.split("完整解答", 1)[1]
    return clean_math(value)


def truncate_clean_text(text: str | None, limit: int) -> tuple[str, bool]:
    """Truncate cleaned text without splitting an embedded math placeholder."""
    value = clean_math(text)
    if len(value) <= limit:
        return value, False
    pieces = re.split(r"(ZZMATH\d+ZZ)", value)
    output: list[str] = []
    remaining = limit
    for piece in pieces:
        if not piece or remaining <= 0:
            continue
        if re.fullmatch(r"ZZMATH\d+ZZ", piece):
            if len(piece) > remaining:
                break
            output.append(piece)
            remaining -= len(piece)
        else:
            output.append(piece[:remaining])
            remaining -= min(len(piece), remaining)
    return "".join(output).rstrip(), True


def paragraph_text(text: str | None, font_size: float = 11.0) -> str:
    escaped = html.escape(clean_math(text)).replace("\n", "<br/>")
    return re.sub(
        r"ZZMATH\d+ZZ",
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
        leading=22, textColor=colors.HexColor("#101828"), spaceAfter=3 * mm,
    )
    meta = ParagraphStyle(
        "meta-cn", parent=body, fontSize=8.5, leading=15,
        textColor=colors.HexColor("#667085"), spaceAfter=4 * mm,
    )

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(18 * mm, 12 * mm, f"高中数学错题本 · {error_id}")
        canvas.drawRightString(192 * mm, 12 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    # 题图：裁掉空白边、按 DPI 换算物理尺寸、只缩小不放大。
    max_img_w = 110 * mm
    max_img_h = 65 * mm
    pdf_tmp_root = PROJECT_ROOT / "tmp" / "pdfs"
    pdf_tmp_root.mkdir(parents=True, exist_ok=True)
    diagram_temp_dir = Path(tempfile.mkdtemp(prefix="diagrams-", dir=pdf_tmp_root))

    def image_flowable(rel_path: str):
        path = Path(rel_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / rel_path
        if not path.is_file():
            return None
        prepared = prepare_diagram_image(path, diagram_temp_dir, max_img_w, max_img_h)
        if prepared is None:
            return None
        prepared_path, width, height = prepared
        image = RLImage(str(prepared_path), width=width, height=height)
        image.hAlign = "CENTER"
        return image

    def text_flowables(text: str | None, style, font_size: float = 11.0, prefix: str = ""):
        cleaned, img_paths = split_stem_images(text)
        flows = [Paragraph(prefix + paragraph_text(cleaned, font_size), style)]
        for rel in img_paths:
            flowable = image_flowable(rel)
            if flowable is None:
                flows.append(Paragraph("［题图缺失，见原卷］", meta))
            else:
                flows.extend([Spacer(1, 2 * mm), flowable, Spacer(1, 3 * mm)])
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
            solution, truncated = truncate_clean_text(solution_stripped, solution_chars)
            if truncated:
                solution += "……（完整解析保存在题库中）"
            story.extend([
                Paragraph(f"{row['rank']}　{row['question_id']}", heading),
                Paragraph(f"<b>答案：</b>{paragraph_text(row['answer'])}", body),
                Paragraph(paragraph_text(solution) or "题库暂无解析。", body),
            ])
            for rel in solution_images:
                flowable = image_flowable(rel)
                story.append(flowable if flowable is not None else Paragraph("［题图缺失，见原卷］", meta))

    try:
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
    finally:
        shutil.rmtree(diagram_temp_dir, ignore_errors=True)


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
    ensure_pdf_runtime()
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
