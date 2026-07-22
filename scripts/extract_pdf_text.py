#!/usr/bin/env python3
"""Extract page-separated text from PDFs for source auditing."""

from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    reader = PdfReader(args.pdf)
    chunks = []
    for page_number, page in enumerate(reader.pages, 1):
        chunks.append(f"\n\n===== PAGE {page_number} =====\n\n{page.extract_text() or ''}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(chunks), encoding="utf-8")
    print(f"pages={len(reader.pages)} chars={sum(len(chunk) for chunk in chunks)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
