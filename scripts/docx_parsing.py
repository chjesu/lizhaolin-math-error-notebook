"""Shared, database-free helpers for parsing extracted DOCX exam text."""

from __future__ import annotations

import re


def clean_math_segment(value: str) -> str:
    replacements = {
        "//": r"\parallel ", "⊥": r"\perp ", "≤": r"\le ", "≥": r"\ge ",
        "≠": r"\ne ", "∞": r"\infty ", "∈": r"\in ", "∉": r"\notin ",
        "∀": r"\forall ", "∃": r"\exists ", "∪": r"\cup ", "∩": r"\cap ",
        "⊆": r"\subseteq ", "⊂": r"\subset ", "π": r"\pi ", "θ": r"\theta ",
        "⊄": r"\not\subset ", "ξ": r"\xi ", "Ω": r"\Omega ",
        "Γ": r"\Gamma ", "×": r"\times ", "⋅": r"\cdot ", "−": "-",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    value = value.replace("^{'}", "^{\\prime}")
    value = re.sub(r"([A-Za-z]+)\^\{\\to\s*\}", r"\\vec{\1}", value)
    for name in ("sin", "cos", "tan", "ln", "lg", "log", "min", "max"):
        value = re.sub(rf"(?<![A-Za-z\\]){name}", rf"\\{name} ", value)
    value = value.replace("N^{*}", r"\mathbb{N}^{*}")
    return re.sub(r"[ \t]+", " ", value).strip()


def clean_latex(value: str) -> str:
    parts = value.split("$")
    for index in range(1, len(parts), 2):
        parts[index] = clean_math_segment(parts[index])
    value = "$".join(parts)
    value = value.replace(r"\right$", r"\right.$")
    value = value.replace(
        r"\left\{ \left( x,y\right)\left| \right|x-a∣\le 1,\left| y-b\right|\le 1\right\}",
        r"\{(x,y)\mid |x-a|\le 1,\ |y-b|\le 1\}",
    )
    value = value.replace("（    ）", "（ ）").replace("（     ）", "（ ）").replace("（   ）", "（ ）")
    return value.strip()


OPTION_MARKER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[（(]\s*([A-D])\s*[）)]|([A-D])\s*[．.、])"
)


def _option_label(match: re.Match[str]) -> str:
    return match.group(1) or match.group(2)


def split_options(lines: list[str]) -> tuple[list[str], list[str]]:
    """Split A-D choices while preserving a same-paragraph stem prefix."""
    labels: list[str] = []
    for line in lines:
        matches = list(OPTION_MARKER_RE.finditer(line))
        labels.extend(_option_label(match) for match in matches)
    if not labels or labels[0] != "A" or "B" not in labels:
        return [], list(lines)

    option_chunks: list[str] = []
    remaining: list[str] = []
    for line in lines:
        matches = list(OPTION_MARKER_RE.finditer(line))
        if not matches:
            remaining.append(line)
            continue
        prefix = line[:matches[0].start()].strip()
        if prefix:
            remaining.append(prefix)
        option_chunks.append(line[matches[0].start():].strip())

    option_text = " ".join(option_chunks)
    matches = list(OPTION_MARKER_RE.finditer(option_text))
    options: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(option_text)
        options.append(_option_label(match) + "．" + option_text[match.end():end].strip())
    return options, remaining
