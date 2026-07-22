"""Apply item-level question reviews through notebook.py.

The manifest is a JSON object with a ``questions`` array.  Every approved
record must contain ``id`` and a substantive ``review_note``.  The current
database solution is copied into an individual audit file unless the record
provides ``solution_override``.  Database writes are performed only by
``notebook.py annotate`` and every question is promoted separately.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--notebook", type=Path, required=True)
    parser.add_argument("--solutions-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = payload.get("questions", [])
    if not isinstance(records, list) or not records:
        raise ValueError("manifest questions must be a non-empty list")

    db_uri = args.db.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True)
    conn.row_factory = sqlite3.Row
    rows = {
        row["id"]: row
        for row in conn.execute(
            "SELECT id,stem,answer,solution,source_name,verified FROM questions "
            f"WHERE id IN ({','.join('?' for _ in records)})",
            tuple(str(record.get("id") or "") for record in records),
        )
    }
    conn.close()

    prepared: list[dict[str, str]] = []
    skipped_verified: list[str] = []
    seen: set[str] = set()
    for record in records:
        question_id = str(record.get("id") or "").strip()
        note = str(record.get("review_note") or "").strip()
        if not question_id or question_id in seen:
            raise ValueError(f"missing or duplicate id: {question_id!r}")
        if len(note) < 18:
            raise ValueError(f"review_note is not substantive for {question_id}")
        question = rows.get(question_id)
        if question is None:
            raise ValueError(f"question not found: {question_id}")
        if question["verified"]:
            skipped_verified.append(question_id)
            seen.add(question_id)
            continue
        solution = str(record.get("solution_override") or question["solution"] or "").strip()
        if not solution:
            raise ValueError(f"reviewed solution missing: {question_id}")
        source_expected = str(record.get("source_name") or "").strip()
        if source_expected and source_expected != question["source_name"]:
            raise ValueError(
                f"source mismatch for {question_id}: {question['source_name']!r}"
            )
        audit_text = (
            "逐题审核解答\n\n"
            f"- 题目编号：`{question_id}`\n"
            f"- 来源：{question['source_name']}\n"
            "- 审核结论：题干、答案与解题过程经逐题核对后通过。\n"
            f"- 独立核对要点：{note}\n\n"
            "完整解答\n\n"
            f"{solution}\n"
        )
        prepared.append(
            {
                "id": question_id,
                "text": audit_text,
                "stem_override": str(record.get("stem_override") or "").strip(),
                "answer_override": str(record.get("answer_override") or "").strip(),
            }
        )
        seen.add(question_id)

    print(json.dumps({"prepared": len(prepared), "already_verified": skipped_verified, "dry_run": args.dry_run}, ensure_ascii=False))
    if args.dry_run:
        return

    args.solutions_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for item in prepared:
        solution_path = args.solutions_dir / f"{item['id']}.md"
        solution_path.write_text(item["text"], encoding="utf-8")
        command = [
            sys.executable,
            "-B",
            str(args.notebook),
            "annotate",
            item["id"],
            "--solution-file",
            str(solution_path),
        ]
        if item["stem_override"]:
            stem_path = args.solutions_dir / f"{item['id']}-stem.md"
            stem_path.write_text(item["stem_override"] + "\n", encoding="utf-8")
            command.extend(["--stem-file", str(stem_path)])
        if item["answer_override"]:
            answer_path = args.solutions_dir / f"{item['id']}-answer.md"
            answer_path.write_text(item["answer_override"] + "\n", encoding="utf-8")
            command.extend(["--answer-file", str(answer_path)])
        command.extend(["--verify", "--json"])
        child_env = os.environ.copy()
        child_env["PYTHONUTF8"] = "1"
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
        if completed.returncode:
            raise RuntimeError(
                f"annotate failed for {item['id']}: {completed.stdout}{completed.stderr}"
            )
        results.append(json.loads(completed.stdout))
    print(json.dumps({"verified": len(results), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
