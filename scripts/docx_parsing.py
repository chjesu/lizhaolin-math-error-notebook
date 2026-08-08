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
    """Split the last complete A-D choice block and preserve its stem prefix.

    Point references such as ``A、B两点`` and ``C、D`` can use the same
    punctuation as option labels.  A real choice block must therefore be an
    ordered A-B-C-D quartet; choosing the last complete quartet keeps those
    earlier point references in the stem.
    """
    joined = "\n".join(lines)
    matches = list(OPTION_MARKER_RE.finditer(joined))
    labels = [_option_label(match) for match in matches]
    starts = [
        index
        for index in range(max(0, len(matches) - 3))
        if labels[index : index + 4] == list("ABCD")
    ]
    if not starts:
        return [], list(lines)
    start = starts[-1]
    selected = matches[start : start + 4]
    prefix = joined[: selected[0].start()].strip()
    remaining = [line.strip() for line in prefix.splitlines() if line.strip()]
    options: list[str] = []
    for index, match in enumerate(selected):
        end = selected[index + 1].start() if index + 1 < len(selected) else len(joined)
        options.append(_option_label(match) + "．" + joined[match.end():end].strip())
    return options, remaining
