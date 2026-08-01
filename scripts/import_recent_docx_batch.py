"""Batch-extract and import recent authorized DOCX math exams with compact output.

This is an orchestration layer only: OMML extraction/building stays in the
existing scripts and all database reads/writes go through notebook.py CLI.
Questions are always imported unverified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / ".agents" / "skills" / "math-error-notebook" / "scripts" / "notebook.py"
EXTRACT = ROOT / "scripts" / "extract_docx_omml.py"
BUILD = ROOT / "scripts" / "build_omml_exam_import.py"


def run_json(command: list[str]) -> dict[str, Any] | list[dict[str, Any]]:
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail[-1200:] or f"command failed: {command[0]}")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("command returned no JSON")
    return json.loads(lines[-1])


def source_name(path: Path) -> str:
    value = re.sub(r"\s*[（(]\d+[）)]$", "", path.stem).strip()
    return re.sub(r"\s+", " ", value)


def infer_grade(name: str) -> int:
    if "高一" in name:
        return 10
    if "高二" in name:
        return 11
    national_exam = re.search(r"(?:新课标)?全国[ⅠⅡⅢ一二三甲乙12]*卷.*数学.*真题", name)
    if "高三" in name or "高考" in name or national_exam or re.search(r"202\d届", name):
        return 12
    raise ValueError("cannot infer grade")


def infer_semester(name: str) -> int:
    second_markers = ("第二学期", "下学期", "2月", "3月", "4月", "5月", "二模", "一模")
    return 2 if any(marker in name for marker in second_markers) else 1


def infer_year(name: str) -> str:
    match = re.search(r"(20\d{2})\s*[-—–至]\s*(20\d{2})", name)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    match = re.search(r"(20\d{2})届", name)
    if match:
        return match.group(1)
    match = re.search(r"(20\d{2})年", name)
    return match.group(1) if match else "2025-2026"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def docx_content_sha256(path: Path) -> str:
    """Hash question-bearing DOCX content while ignoring package metadata."""
    digest = hashlib.sha256()
    with zipfile.ZipFile(path) as archive:
        members = [
            name
            for name in archive.namelist()
            if name == "word/document.xml" or name.startswith("word/media/")
        ]
        if "word/document.xml" not in members:
            raise ValueError(f"DOCX has no word/document.xml: {path}")
        for name in sorted(members):
            payload = archive.read(name)
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.hexdigest()


def recent_docx(directory: Path, start: date, end: date) -> list[Path]:
    return sorted(
        (
            path for path in directory.iterdir()
            if path.is_file()
            and path.suffix.casefold() == ".docx"
            and "数学" in path.name
            and start <= datetime.fromtimestamp(path.stat().st_mtime).date() <= end
        ),
        key=lambda path: (path.stat().st_mtime, path.name),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--to-date", type=date.fromisoformat, required=True)
    parser.add_argument("--batch-name", required=True)
    parser.add_argument("--license", default="User-Provided-Authorized")
    parser.add_argument("--import", dest="do_import", action="store_true")
    args = parser.parse_args()

    if not args.directory.is_dir():
        raise SystemExit(f"directory not found: {args.directory}")
    batch_dir = ROOT / "data" / "imports" / args.batch_name
    batch_dir.mkdir(parents=True, exist_ok=True)
    existing_rows = run_json([sys.executable, "-B", str(NOTEBOOK), "sources", "--json"])
    existing = {
        str(row["name"]): int(row["questions"] or 0)
        for row in existing_rows
        if isinstance(row, dict)
    }
    seen_content_hashes: dict[str, str] = {}
    records: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    total_inserted = 0
    total_duplicates = 0

    for index, path in enumerate(recent_docx(args.directory, args.from_date, args.to_date), 1):
        name = source_name(path)
        file_hash = sha256(path)
        content_hash = docx_content_sha256(path)
        entry: dict[str, Any] = {
            "file": str(path.resolve()),
            "source_name": name,
            "sha256": file_hash,
            "content_sha256": content_hash,
            "modified_date": datetime.fromtimestamp(path.stat().st_mtime).date().isoformat(),
        }
        if content_hash in seen_content_hashes:
            entry.update(
                status="skipped_duplicate_file",
                duplicate_kind="docx_content",
                duplicate_of=seen_content_hashes[content_hash],
            )
            records.append(entry)
            continue
        seen_content_hashes[content_hash] = str(path.resolve())
        if existing.get(name, 0) > 0:
            entry.update(status="skipped_existing_source", existing_questions=existing[name])
            records.append(entry)
            continue
        try:
            grade = infer_grade(name)
            relative_dir = f"{index:02d}-{file_hash[:10]}"
            exam_dir = batch_dir / relative_dir
            exam_dir.mkdir(parents=True, exist_ok=True)
            extract_summary = run_json([
                sys.executable, "-B", str(EXTRACT), str(path),
                "--json", str(exam_dir / "omml_extract.json"),
                "--markdown", str(exam_dir / "omml_extract.md"),
                "--media-dir", str(exam_dir / "media"),
            ])
            build_summary = run_json([
                sys.executable, "-B", str(BUILD), str(exam_dir),
                "--relative-dir", relative_dir,
                "--batch-name", args.batch_name,
                "--grade", str(grade),
                "--semester", str(infer_semester(name)),
                "--source-year", infer_year(name),
            ])
            entry.update(
                status="extracted" if not args.do_import else "ready_to_import",
                relative_dir=relative_dir,
                grade=grade,
                paragraphs=extract_summary["paragraphs"],
                extracted_questions=build_summary["questions"],
                with_answers=build_summary["with_answers"],
                with_solutions=build_summary["with_solutions"],
            )
            if args.do_import:
                imported = run_json([
                    sys.executable, "-B", str(NOTEBOOK), "import-file",
                    str(exam_dir / "questions.jsonl"),
                    "--source-name", name,
                    "--source-url", str(path.resolve()),
                    "--license", args.license,
                    "--rights-confirmed", "--json",
                ])
                entry.update(status="imported", import_result=imported)
                total_inserted += int(imported["inserted"])
                total_duplicates += int(imported["duplicates"])
                existing[name] = int(imported["inserted"])
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            entry.update(status="failed", error=str(exc))
            failures.append({"source_name": name, "error": str(exc)})
        records.append(entry)

    manifest = {
        "schema": "math-docx-import-batch/v1",
        "batch_name": args.batch_name,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "authorized_by_user": True,
        "license": args.license,
        "date_range": [args.from_date.isoformat(), args.to_date.isoformat()],
        "files": records,
    }
    manifest_path = batch_dir / "batch_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "files_found": len(records),
        "imported_sources": sum(row["status"] == "imported" for row in records),
        "skipped_existing_sources": sum(row["status"] == "skipped_existing_source" for row in records),
        "skipped_duplicate_files": sum(row["status"] == "skipped_duplicate_file" for row in records),
        "failed_sources": len(failures),
        "questions_inserted": total_inserted,
        "duplicate_questions": total_duplicates,
        "manifest": str(manifest_path.resolve()),
        "failures": failures,
    }
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
