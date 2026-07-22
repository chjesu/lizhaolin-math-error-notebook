#!/usr/bin/env python3
"""Read-only comparison of a candidate question bank against the canonical bank."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


QUESTION_COLUMNS = (
    "id", "stem", "options_json", "answer", "solution", "grade", "semester",
    "curriculum", "question_type", "difficulty", "source_name", "source_url",
    "source_year", "license", "verified", "fingerprint", "created_at", "raw_json",
)


def connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_stem(text: str) -> str:
    return re.sub(r"[\s，。！？；：,.!?;:()（）\\$]", "", text).lower()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def question_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {row["id"]: dict(row) for row in conn.execute("SELECT * FROM questions")}


def related(conn: sqlite3.Connection, question_id: str) -> tuple[list[str], list[str]]:
    knowledge = [row[0] for row in conn.execute(
        "SELECT knowledge_code FROM question_knowledge WHERE question_id=? ORDER BY knowledge_code",
        (question_id,),
    )]
    targets = [row[0] for row in conn.execute(
        "SELECT cause_code FROM question_targets WHERE question_id=? ORDER BY cause_code",
        (question_id,),
    )]
    return knowledge, targets


def export_record(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    knowledge, targets = related(conn, row["id"])
    options = json.loads(row["options_json"]) if row.get("options_json") else None
    return {
        "candidate_id": row["id"],
        "stem": row["stem"],
        "options": options,
        "answer": row["answer"],
        "solution": row["solution"],
        "grade": row["grade"],
        "semester": row["semester"],
        "curriculum": row["curriculum"],
        "question_type": row["question_type"],
        "difficulty": row["difficulty"],
        "source_name": row["source_name"],
        "source_url": row["source_url"],
        "source_year": row["source_year"],
        "license": row["license"],
        "candidate_verified": bool(row["verified"]),
        "knowledge_codes": knowledge,
        "target_causes": targets,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("canonical", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    canonical = connect_readonly(args.canonical)
    candidate = connect_readonly(args.candidate)
    canonical_rows = question_map(canonical)
    candidate_rows = question_map(candidate)

    inserted_ids = sorted(set(candidate_rows) - set(canonical_rows))
    deleted_ids = sorted(set(canonical_rows) - set(candidate_rows))
    shared_ids = sorted(set(canonical_rows) & set(candidate_rows))
    changed: list[dict[str, Any]] = []
    field_change_counts: dict[str, int] = {}
    for question_id in shared_ids:
        before = canonical_rows[question_id]
        after = candidate_rows[question_id]
        fields = [name for name in QUESTION_COLUMNS if before.get(name) != after.get(name)]
        if fields:
            semantic_fields = [
                name for name in fields
                if name in {"stem", "options_json", "answer", "solution", "question_type", "difficulty", "verified"}
            ]
            changed.append({
                "id": question_id,
                "stem": before.get("stem"),
                "source_name": before.get("source_name"),
                "solution": before.get("solution"),
                "changed_fields": fields,
                "changes": {
                    name: {"canonical": before.get(name), "candidate": after.get(name)}
                    for name in semantic_fields
                },
            })
            for field in fields:
                field_change_counts[field] = field_change_counts.get(field, 0) + 1

    canonical_stems = {normalize_stem(row["stem"]): qid for qid, row in canonical_rows.items()}
    canonical_normalized = [(qid, normalize_stem(row["stem"])) for qid, row in canonical_rows.items()]
    inserted = []
    for question_id in inserted_ids:
        record = export_record(candidate, candidate_rows[question_id])
        normalized = normalize_stem(record["stem"])
        record["same_normalized_stem_as"] = canonical_stems.get(normalized)
        closest_id, closest_ratio = max(
            ((qid, SequenceMatcher(None, normalized, other).ratio()) for qid, other in canonical_normalized),
            key=lambda pair: pair[1],
        )
        record["closest_canonical"] = {"id": closest_id, "similarity": round(closest_ratio, 4)}
        inserted.append(record)

    missing_solution = sum(not (row.get("solution") or "").strip() for row in candidate_rows.values())
    missing_knowledge = sum(not related(candidate, qid)[0] for qid in candidate_rows)
    missing_targets = sum(not related(candidate, qid)[1] for qid in candidate_rows)
    report = {
        "canonical": {"path": str(args.canonical.resolve()), "sha256": digest(args.canonical), "questions": len(canonical_rows)},
        "candidate": {"path": str(args.candidate.resolve()), "sha256": digest(args.candidate), "questions": len(candidate_rows)},
        "summary": {
            "inserted": len(inserted_ids),
            "deleted": len(deleted_ids),
            "changed": len(changed),
            "field_change_counts": dict(sorted(field_change_counts.items())),
            "candidate_missing_solution": missing_solution,
            "candidate_missing_knowledge": missing_knowledge,
            "candidate_missing_targets": missing_targets,
        },
        "inserted_questions": inserted,
        "deleted_ids": deleted_ids,
        "changed_questions": changed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
