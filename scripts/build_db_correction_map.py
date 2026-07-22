"""Map regenerated source records to the question bank's internal IDs.

The database is opened read-only.  The produced JSON is consumed by
notebook.py annotate, which remains the only writer.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--source", action="append", required=True, help="directory=source name")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source_map = dict(value.split("=", 1) for value in args.source)
    regenerated: dict[tuple[str, str], dict[str, object]] = {}
    for directory, source_name in source_map.items():
        path = args.batch_root / directory / "questions.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            regenerated[(source_name, str(record["id"]))] = record

    db_uri = args.db.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in source_map)
    rows = conn.execute(
        f"SELECT id,source_name,raw_json FROM questions WHERE source_name IN ({placeholders})",
        tuple(source_map.values()),
    ).fetchall()
    conn.close()

    corrections = []
    missing = []
    for row in rows:
        raw = json.loads(row["raw_json"] or "{}")
        raw_id = str(raw.get("id") or "")
        replacement = regenerated.get((row["source_name"], raw_id))
        if replacement is None:
            missing.append({"db_id": row["id"], "source": row["source_name"], "raw_id": raw_id})
            continue
        correction = dict(replacement)
        correction["id"] = row["id"]
        corrections.append(correction)

    payload = {"questions": corrections, "missing": missing}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"db_rows": len(rows), "corrections": len(corrections), "missing": len(missing), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
