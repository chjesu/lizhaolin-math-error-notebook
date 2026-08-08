"""Prepare and structurally audit every imported DOCX question without DB writes.

The script removes repetitive model work (packet creation, completeness checks,
image-path checks, and exception routing). Mathematical verification still goes
through item-level reviews and notebook.py's verifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / ".agents" / "skills" / "math-error-notebook" / "scripts" / "notebook.py"
BLOCKING_ISSUES = {
    "diagram_reference",
    "missing_solution",
    "missing_tags",
    "missing_targets",
    "missing_choice_options",
    "private_use_glyph",
    "broken_marker",
    "placeholder_answer",
}
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail[-1600:] or "command failed")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("command returned no JSON")
    payload = json.loads(lines[-1])
    if not isinstance(payload, dict):
        raise RuntimeError("command returned unexpected JSON")
    return payload


def safe_name(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def image_problems(question: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    option_text = "\n".join(str(item) for item in (question.get("options") or []))
    combined = "\n".join([
        str(question.get("stem") or ""),
        str(question.get("stored_answer") or ""),
        str(question.get("stored_solution") or ""),
        option_text,
    ])
    references = IMAGE_RE.findall(combined)
    if "如图" in combined and not references:
        problems.append("diagram_reference_without_local_image")
    for reference in references:
        path_text = reference.strip().strip("<>").split(maxsplit=1)[0]
        if re.match(r"^[a-z]+://", path_text, re.IGNORECASE):
            problems.append("remote_image_reference")
            continue
        image_path = Path(path_text)
        if not image_path.is_absolute():
            image_path = ROOT / image_path
        if not image_path.is_file():
            problems.append(f"missing_image:{path_text}")
    return problems


def structural_problems(packet: dict[str, Any]) -> list[str]:
    question = packet["question"]
    checks = packet["automatic_checks"]
    problems = [
        issue for issue in checks.get("issues") or [] if issue in BLOCKING_ISSUES
    ]
    stem = str(question.get("stem") or "").strip()
    answer = str(question.get("stored_answer") or "").strip()
    solution = str(question.get("stored_solution") or "").strip()
    if len(stem) < 8:
        problems.append("stem_too_short")
    if not answer:
        problems.append("missing_answer")
    if len(solution) < 8:
        problems.append("solution_too_short")
    for field, value in (("stem", stem), ("answer", answer), ("solution", solution)):
        if value.count("$") % 2:
            problems.append(f"unbalanced_math_delimiter:{field}")
    qtype = str(question.get("question_type") or "")
    options = question.get("options")
    if "选择" in qtype:
        if not isinstance(options, list) or len(options) < 2:
            problems.append("invalid_choice_options")
        compact_answer = re.sub(r"[\s,，、;；]", "", answer).upper()
        if not compact_answer or re.search(r"[^A-D]", compact_answer):
            problems.append("invalid_choice_answer")
    if not question.get("knowledge_codes"):
        problems.append("missing_knowledge_codes")
    if not question.get("target_causes"):
        problems.append("missing_target_causes")
    if not question.get("feature_codes"):
        problems.append("missing_feature_codes")
    if not question.get("source_record") and not packet.get("source_record"):
        problems.append("missing_source_record")
    source = packet.get("source_record") or {}
    if not source.get("rights_confirmed") or not source.get("license"):
        problems.append("source_rights_not_confirmed")
    problems.extend(image_problems(question))
    return sorted(set(problems))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("import_manifest", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--reviewer", default="codex")
    args = parser.parse_args()

    source_manifest = json.loads(args.import_manifest.read_text(encoding="utf-8"))
    files = source_manifest.get("files") or []
    sources = [
        item["source_name"]
        for item in files
        if item.get("status") == "imported"
        and int((item.get("import_result") or {}).get("inserted") or 0) > 0
    ]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    audited: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for source_name in sources:
        source_dir = args.out_dir / safe_name(source_name)
        try:
            prepared = run_json([
                sys.executable,
                "-B",
                str(NOTEBOOK),
                "prepare-audit-batch",
                "--source-name",
                source_name,
                "--limit",
                "100",
                "--out-dir",
                str(source_dir),
                "--reviewer",
                args.reviewer,
                "--force",
                "--json",
            ])
            work_manifest = json.loads(
                Path(prepared["manifest"]).read_text(encoding="utf-8")
            )
            for item in work_manifest["items"]:
                packet = json.loads(Path(item["packet"]).read_text(encoding="utf-8"))
                problems = structural_problems(packet)
                audited.append({
                    "question_id": item["question_id"],
                    "source_name": source_name,
                    "packet": item["packet"],
                    "review": item["review"],
                    "status": "ready_for_math_review" if not problems else "blocked",
                    "problems": problems,
                })
        except (OSError, KeyError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            failures.append({"source_name": source_name, "error": str(exc)})

    manifest = {
        "schema": "math-structural-audit-batch/v1",
        "batch_name": source_manifest.get("batch_name"),
        "source_manifest": str(args.import_manifest.resolve()),
        "items": audited,
        "failures": failures,
        "database_modified": False,
    }
    manifest_path = args.out_dir / "structural_audit_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        "sources": len(sources),
        "audited": len(audited),
        "ready_for_math_review": sum(
            item["status"] == "ready_for_math_review" for item in audited
        ),
        "blocked": sum(item["status"] == "blocked" for item in audited),
        "failed_sources": len(failures),
        "manifest": str(manifest_path.resolve()),
        "database_modified": False,
    }
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
