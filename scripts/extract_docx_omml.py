"""Extract DOCX paragraphs while converting Microsoft OMML equations to LaTeX.

This deliberately reads the OOXML package directly.  Plain-text DOCX extractors
discard the tree structure that distinguishes numerators, denominators, roots,
limits, and delimiters.
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def qname(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def attr_val(node: ET.Element | None, name: str, default: str = "") -> str:
    if node is None:
        return default
    return node.attrib.get(qname("m", name), node.attrib.get(qname("w", name), default))


def children_named(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in node if local_name(child.tag) == name]


def first_named(node: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in node if local_name(child.tag) == name), None)


def math_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return "".join((part.text or "") for part in node.iter() if local_name(part.tag) == "t")


def brace(value: str) -> str:
    return "{" + value + "}"


def latex_escape_text(value: str) -> str:
    # OMML math runs already contain mathematical Unicode.  Preserve it while
    # escaping only characters that would break LaTeX grouping.
    return value.replace("\\", r"\backslash ").replace("{", r"\{").replace("}", r"\}")


def normalize_math(value: str) -> str:
    replacements = {
        "≤": r"\le ",
        "≥": r"\ge ",
        "≠": r"\ne ",
        "−": "-",
        "′": r"\prime ",
        "≈": r"\approx ",
        "∈": r"\in ",
        "∉": r"\notin ",
        "⊆": r"\subseteq ",
        "⊂": r"\subset ",
        "∪": r"\cup ",
        "∩": r"\cap ",
        "∞": r"\infty ",
        "π": r"\pi ",
        "∀": r"\forall ",
        "∃": r"\exists ",
        "⋅": r"\cdot ",
        "×": r"\times ",
        "÷": r"\div ",
        "→": r"\to ",
        "⇒": r"\Rightarrow ",
        "⇔": r"\Leftrightarrow ",
        "∥": r"\parallel ",
        "⊥": r"\perp ",
        "△": r"\triangle ",
        "∠": r"\angle ",
        "∑": r"\sum ",
        "∫": r"\int ",
        "∏": r"\prod ",
        "…": r"\ldots ",
        "⋯": r"\cdots ",
        "±": r"\pm ",
        "θ": r"\theta ",
        "ξ": r"\xi ",
        "Ω": r"\Omega ",
        "Γ": r"\Gamma ",
    }
    result = latex_escape_text(value)
    for old, new in replacements.items():
        result = result.replace(old, new)
    return result


def cleanup_formula(value: str) -> str:
    for name in ("sin", "cos", "tan", "ln", "lg", "log", "min", "max"):
        value = re.sub(rf"(?<![A-Za-z\\]){name}(?![A-Za-z])", rf"\\{name} ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def omml_to_latex(node: ET.Element | None) -> str:
    if node is None:
        return ""
    name = local_name(node.tag)

    if name in {"oMath", "oMathPara", "e", "num", "den", "sub", "sup", "deg", "lim"}:
        return "".join(omml_to_latex(child) for child in node)
    if name == "r":
        return normalize_math(math_text(node))
    if name in {"rPr", "oMathParaPr", "ctrlPr", "dPr", "radPr", "accPr", "barPr", "naryPr"}:
        return ""
    if name == "f":
        return r"\frac" + brace(omml_to_latex(first_named(node, "num"))) + brace(omml_to_latex(first_named(node, "den")))
    if name == "sSub":
        return omml_to_latex(first_named(node, "e")) + "_" + brace(omml_to_latex(first_named(node, "sub")))
    if name == "sSup":
        return omml_to_latex(first_named(node, "e")) + "^" + brace(omml_to_latex(first_named(node, "sup")))
    if name == "sSubSup":
        return (
            omml_to_latex(first_named(node, "e"))
            + "_"
            + brace(omml_to_latex(first_named(node, "sub")))
            + "^"
            + brace(omml_to_latex(first_named(node, "sup")))
        )
    if name == "rad":
        rad_pr = first_named(node, "radPr")
        hidden = attr_val(first_named(rad_pr, "degHide") if rad_pr is not None else None, "val", "0") in {"1", "true"}
        degree = omml_to_latex(first_named(node, "deg"))
        expression = omml_to_latex(first_named(node, "e"))
        return r"\sqrt" + ("[" + degree + "]" if degree and not hidden else "") + brace(expression)
    if name == "d":
        props = first_named(node, "dPr")
        begin = attr_val(first_named(props, "begChr") if props is not None else None, "val", "(")
        end = attr_val(first_named(props, "endChr") if props is not None else None, "val", ")")
        separator = attr_val(first_named(props, "sepChr") if props is not None else None, "val", "|")
        args = [omml_to_latex(arg) for arg in children_named(node, "e")]
        middle = (" " + normalize_math(separator) + " ").join(args)
        delimiter_map = {"{": r"\{", "}": r"\}", "⌊": r"\lfloor", "⌋": r"\rfloor", "⌈": r"\lceil", "⌉": r"\rceil"}
        left = delimiter_map.get(begin, normalize_math(begin)) if begin else "."
        right = delimiter_map.get(end, normalize_math(end)) if end else "."
        return r"\left" + left + " " + middle + r"\right" + right
    if name == "acc":
        props = first_named(node, "accPr")
        mark = attr_val(first_named(props, "chr") if props is not None else None, "val", "^")
        command = {"¯": r"\overline", "̅": r"\overline", "̂": r"\hat", "^": r"\hat", "˙": r"\dot", "¨": r"\ddot", "⃗": r"\vec", "⃑": r"\vec", "→": r"\vec"}.get(mark, r"\widehat")
        return command + brace(omml_to_latex(first_named(node, "e")))
    if name == "bar":
        props = first_named(node, "barPr")
        position = attr_val(first_named(props, "pos") if props is not None else None, "val", "top")
        command = r"\underline" if position == "bot" else r"\overline"
        return command + brace(omml_to_latex(first_named(node, "e")))
    if name == "nary":
        props = first_named(node, "naryPr")
        char = attr_val(first_named(props, "chr") if props is not None else None, "val", "∫")
        op = {"∫": r"\int", "∑": r"\sum", "∏": r"\prod", "∪": r"\bigcup", "∩": r"\bigcap"}.get(char, normalize_math(char))
        sub = omml_to_latex(first_named(node, "sub"))
        sup = omml_to_latex(first_named(node, "sup"))
        expression = omml_to_latex(first_named(node, "e"))
        return op + ("_" + brace(sub) if sub else "") + ("^" + brace(sup) if sup else "") + " " + expression
    if name == "limLow":
        return omml_to_latex(first_named(node, "e")) + "_" + brace(omml_to_latex(first_named(node, "lim")))
    if name == "limUpp":
        return omml_to_latex(first_named(node, "e")) + "^" + brace(omml_to_latex(first_named(node, "lim")))
    if name == "eqArr":
        rows = [omml_to_latex(arg) for arg in children_named(node, "e")]
        return r"\begin{aligned}" + r" \\ ".join(rows) + r"\end{aligned}"

    # Formatting-only or uncommon wrappers: keep their mathematical children.
    return "".join(omml_to_latex(child) for child in node)


def parse_relationships(zf: zipfile.ZipFile) -> dict[str, str]:
    rel_path = "word/_rels/document.xml.rels"
    if rel_path not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read(rel_path))
    return {
        rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
        for rel in root.findall(qname("pr", "Relationship"))
    }


def paragraph_text(paragraph: ET.Element, relationships: dict[str, str]) -> tuple[str, list[str]]:
    images: list[str] = []

    def walk(node: ET.Element) -> str:
        name = local_name(node.tag)
        if name == "oMath":
            return "$" + cleanup_formula(omml_to_latex(node)) + "$"
        if name == "oMathPara":
            return "".join(walk(child) for child in node)
        if name == "t":
            return node.text or ""
        if name == "tab":
            return "\t"
        if name in {"br", "cr"}:
            return "\n"
        if name in {"blip", "imagedata"}:
            rel_id = (
                node.attrib.get(qname("r", "embed"), "")
                or node.attrib.get(qname("r", "id"), "")
                or node.attrib.get(qname("o", "relid"), "")
            )
            target = relationships.get(rel_id, rel_id)
            if not target:
                return ""
            images.append(target)
            return f"[IMAGE:{target}]"
        if name in {"instrText", "delText"}:
            return ""
        return "".join(walk(child) for child in node)

    text = "".join(walk(child) for child in paragraph)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text, images


def extract(input_path: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    with zipfile.ZipFile(input_path) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
        relationships = parse_relationships(zf)
        body = root.find("w:body", NS)
        if body is None:
            return records
        index = 0
        for paragraph in body.iter(qname("w", "p")):
            text, images = paragraph_text(paragraph, relationships)
            if not text and not images:
                continue
            style_node = paragraph.find("w:pPr/w:pStyle", NS)
            style = style_node.attrib.get(qname("w", "val"), "") if style_node is not None else ""
            records.append({"index": index, "style": style, "text": text, "images": images})
            index += 1
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path, required=True)
    parser.add_argument("--markdown", dest="markdown_path", type=Path, required=True)
    parser.add_argument("--media-dir", dest="media_dir", type=Path)
    args = parser.parse_args()

    records = extract(args.input)
    if args.media_dir:
        args.media_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.input) as zf:
            for name in zf.namelist():
                if name.startswith("word/media/") and not name.endswith("/"):
                    (args.media_dir / Path(name).name).write_bytes(zf.read(name))
    args.json_path.parent.mkdir(parents=True, exist_ok=True)
    args.json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"[{record['index']}] {record['text']}" for record in records]
    args.markdown_path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"paragraphs": len(records), "json": str(args.json_path), "markdown": str(args.markdown_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
