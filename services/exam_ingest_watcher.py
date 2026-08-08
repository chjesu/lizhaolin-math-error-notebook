#!/usr/bin/env python3
"""Watch Downloads and route authorized DOCX exams to three notebook projects.

This service is an orchestration layer only.  It never opens or writes a
notebook database directly: every import is performed by the target project's
existing converter and ``notebook.py import-file`` quality gates.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "exam-ingest-watcher.json"
SUBJECT_NAMES = {"math": "数学", "physics": "物理", "chemistry": "化学"}
SUPPORTED_IMPORT_EXTENSIONS = {".docx"}
OBSERVED_EXTENSIONS = {".docx", ".doc", ".docm", ".pdf"}
PARTIAL_SUFFIXES = {".crdownload", ".download", ".part", ".tmp"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_signature(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def _docx_text(path: Path, limit: int = 30000) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            data = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    except (OSError, KeyError, zipfile.BadZipFile):
        return ""
    values = re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", data, flags=re.DOTALL)
    return html.unescape("".join(values))[:limit]


def classify_subject(path: Path) -> tuple[str | None, dict[str, int]]:
    """Classify by filename first, then a small DOCX header-text sample."""
    name = path.name.casefold()
    text = _docx_text(path).casefold() if path.suffix.casefold() == ".docx" else ""
    terms = {
        "math": ("数学试卷", "数学试题", "数学"),
        "physics": ("物理试卷", "物理试题", "物理"),
        "chemistry": ("化学试卷", "化学试题", "化学"),
    }
    scores: dict[str, int] = {}
    for subject, subject_terms in terms.items():
        score = 0
        for index, term in enumerate(subject_terms):
            score += (120 - index * 10) * name.count(term.casefold())
            score += (12 - index * 2) * text.count(term.casefold())
        scores[subject] = score
    best = max(scores.values(), default=0)
    winners = [subject for subject, score in scores.items() if score == best and score > 0]
    return (winners[0] if len(winners) == 1 else None), scores


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def parse_last_json(stdout: str) -> Any:
    for line in reversed(stdout.splitlines()):
        if not line.strip():
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError("command returned no JSON")


class CommandResult:
    def __init__(self, returncode: int, payload: Any, stdout: str, stderr: str):
        self.returncode = returncode
        self.payload = payload
        self.stdout = stdout
        self.stderr = stderr


class QualityGateError(RuntimeError):
    """The source requires correction/manual handling, so blind retry is unsafe."""


def run_json_command(command: list[str], cwd: Path, timeout: int = 600) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    try:
        payload = parse_last_json(completed.stdout)
    except RuntimeError:
        if completed.returncode:
            payload = {"error": (completed.stderr or completed.stdout or "command failed")[-4000:]}
        else:
            raise
    return CommandResult(completed.returncode, payload, completed.stdout, completed.stderr)


class ImportOrchestrator:
    def __init__(self, config: dict[str, Any], runner: Callable[..., CommandResult] = run_json_command):
        self.config = config
        self.runner = runner
        python_value = config.get("python")
        self.python = Path(python_value) if python_value else Path(sys.executable)

    def _project(self, subject: str) -> tuple[Path, dict[str, Any]]:
        entry = self.config["projects"][subject]
        return Path(entry["root"]), entry

    def _run(self, command: list[str], cwd: Path) -> CommandResult:
        result = self.runner(command, cwd=cwd, timeout=int(self.config.get("command_timeout_seconds", 600)))
        if not isinstance(result.payload, (dict, list)):
            raise RuntimeError("project command returned unsupported JSON")
        return result

    def _verify_bank(self, root: Path, notebook: Path) -> dict[str, Any]:
        result = self._run(
            [str(self.python), "-X", "utf8", "-B", str(notebook), "bank-info", "--json"],
            root,
        )
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout)[-1200:])
        info = result.payload
        if info.get("integrity_check") != "ok" or int(info.get("foreign_key_violations", 1)):
            raise RuntimeError("target notebook failed integrity or foreign-key check")
        return info

    @staticmethod
    def _stage_source(source: Path, root: Path, digest: str, subject: str) -> Path:
        input_dir = root / "data" / "auto-ingest" / digest[:16] / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        subject_name = SUBJECT_NAMES[subject]
        staged_name = source.name if subject_name in source.name else f"{subject_name}-{source.name}"
        target = input_dir / staged_name
        if not target.is_file() or file_sha256(target) != digest:
            shutil.copy2(source, target)
        return input_dir

    def import_file(self, subject: str, source: Path, digest: str) -> dict[str, Any]:
        root, project = self._project(subject)
        notebook = root / project["notebook"]
        input_dir = self._stage_source(source, root, digest, subject)
        if subject == "math":
            outcome = self._import_math(root, project, notebook, input_dir, source, digest)
        else:
            outcome = self._import_science(subject, root, project, notebook, input_dir, source, digest)
        outcome["bank"] = self._verify_bank(root, notebook)
        return outcome

    def _import_math(
        self,
        root: Path,
        project: dict[str, Any],
        notebook: Path,
        input_dir: Path,
        source: Path,
        digest: str,
    ) -> dict[str, Any]:
        modified = datetime.fromtimestamp(source.stat().st_mtime).date().isoformat()
        importer = root / project["docx_importer"]
        result = self._run(
            [
                str(self.python), "-X", "utf8", "-B", str(importer), str(input_dir),
                "--from-date", modified, "--to-date", modified,
                "--batch-name", f"auto-download-{digest[:12]}",
                "--license", self.config["license"], "--import",
            ],
            root,
        )
        summary = result.payload
        success = (
            int(summary.get("failed_sources", 0)) == 0
            and int(summary.get("blocked_sources", 0)) == 0
            and (
                int(summary.get("imported_sources", 0))
                + int(summary.get("skipped_existing_sources", 0))
                + int(summary.get("skipped_duplicate_files", 0))
            ) == 1
        )
        if result.returncode or not success:
            raise QualityGateError(
                "math import did not pass its quality gate: "
                + json.dumps(summary, ensure_ascii=False, separators=(",", ":"))[-1800:]
            )
        status = "imported" if int(summary.get("imported_sources", 0)) else "already_imported"
        return {"status": status, "subject": "math", "summary": summary}

    def _import_science(
        self,
        subject: str,
        root: Path,
        project: dict[str, Any],
        notebook: Path,
        input_dir: Path,
        source: Path,
        digest: str,
    ) -> dict[str, Any]:
        converter = root / project["docx_converter"]
        output_root = root / "data" / "raw" / "auto-ingest" / digest[:16]
        empty_known = root / "data" / "auto-ingest" / digest[:16] / "empty-known"
        empty_known.mkdir(parents=True, exist_ok=True)
        converted = self._run(
            [
                str(self.python), "-X", "utf8", "-B", str(converter), str(input_dir),
                "--output-root", str(output_root), "--project-root", str(root),
                "--known-root", str(empty_known), "--limit", "1",
                "--license", self.config["license"], "--rights-confirmed", "--json",
            ],
            root,
        )
        if converted.returncode:
            raise RuntimeError((converted.stderr or converted.stdout)[-1800:])
        manifest = converted.payload
        documents = manifest.get("documents") or []
        summary = manifest.get("summary") or {}
        questions = int(summary.get("questions", 0))
        quality_ok = (
            len(documents) == 1
            and questions > 0
            and int(summary.get("invalid", 0)) == 0
            and int(summary.get("with_solution", 0)) == questions
            and int(summary.get("warnings", 0)) == 0
        )
        if not quality_ok:
            raise QualityGateError(
                f"{subject} converter quality gate blocked import: "
                + json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
            )
        document = documents[0]
        jsonl = root / document["jsonl"]
        imported = self._run(
            [
                str(self.python), "-X", "utf8", "-B", str(notebook),
                "import-file", str(jsonl), "--source-name", document["source_name"],
                "--source-url", str(source.resolve()), "--license", self.config["license"],
                "--rights-confirmed", "--json",
            ],
            root,
        )
        if imported.returncode:
            raise RuntimeError((imported.stderr or imported.stdout)[-1800:])
        result = imported.payload
        handled = int(result.get("inserted", 0)) + int(result.get("duplicates", 0))
        if handled != questions:
            raise QualityGateError(
                f"{subject} import count mismatch: expected {questions}, handled {handled}"
            )
        status = "imported" if int(result.get("inserted", 0)) else "already_imported"
        return {
            "status": status,
            "subject": subject,
            "conversion": summary,
            "import_result": result,
            "manifest": manifest.get("manifest"),
        }


class ExamIngestWatcher:
    def __init__(
        self,
        config: dict[str, Any],
        importer: Callable[[str, Path, str], dict[str, Any]] | None = None,
    ):
        self.config = config
        self.watch_dir = Path(config["watch_dir"])
        self.archive_root = Path(config["archive_root"])
        self.state_path = Path(config["state_file"])
        self.log_path = Path(config["event_log"])
        self.state = load_json(
            self.state_path,
            {"schema": "exam-ingest-watcher-state/v1", "initialized": False, "files": {}},
        )
        orchestrator = ImportOrchestrator(config)
        self.importer = importer or orchestrator.import_file

    def _event(self, event: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"at": now_iso(), **event}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _save(self) -> None:
        self.state["updated_at"] = now_iso()
        write_json_atomic(self.state_path, self.state)

    def _candidate_files(self) -> list[Path]:
        if not self.watch_dir.is_dir():
            raise ValueError(f"watch directory not found: {self.watch_dir}")
        return sorted(
            (
                path for path in self.watch_dir.iterdir()
                if path.is_file()
                and path.suffix.casefold() in OBSERVED_EXTENSIONS
                and not any(path.name.casefold().endswith(suffix) for suffix in PARTIAL_SUFFIXES)
            ),
            key=lambda path: (path.stat().st_mtime_ns, path.name.casefold()),
        )

    def _archive(self, source: Path, subject: str, digest: str) -> Path:
        resolved_watch = self.watch_dir.resolve()
        resolved_source = source.resolve()
        if resolved_source.parent != resolved_watch:
            raise ValueError("refusing to archive a file outside the configured watch directory")
        modified = datetime.fromtimestamp(source.stat().st_mtime)
        directory = self.archive_root / SUBJECT_NAMES[subject] / f"{modified:%Y}" / f"{modified:%m}"
        directory.mkdir(parents=True, exist_ok=True)
        base = f"{modified:%Y-%m-%d}__{digest[:12]}__{source.name}"
        destination = directory / base
        counter = 2
        while destination.exists():
            destination = directory / f"{Path(base).stem}__{counter}{source.suffix}"
            counter += 1
        shutil.move(str(source), str(destination))
        return destination

    def _process(self, path: Path, record: dict[str, Any]) -> None:
        subject, scores = classify_subject(path)
        record["classification_scores"] = scores
        if subject is None:
            record.update(status="classification_required", last_error="unable to determine one subject")
            self._event({"event": "classification_required", "path": str(path), "scores": scores})
            return
        record["subject"] = subject
        if path.suffix.casefold() not in SUPPORTED_IMPORT_EXTENSIONS:
            record.update(
                status="manual_format_required",
                last_error="automatic question import currently requires DOCX; file was not moved",
            )
            self._event({"event": "manual_format_required", "path": str(path), "subject": subject})
            return
        digest = file_sha256(path)
        record["sha256"] = digest
        record["attempts"] = int(record.get("attempts", 0)) + 1
        try:
            outcome = self.importer(subject, path, digest)
            if outcome.get("status") not in {"imported", "already_imported"}:
                raise RuntimeError("importer returned a non-archivable status")
            destination = self._archive(path, subject, digest)
            record.update(
                status="archived",
                archive_path=str(destination.resolve()),
                import_status=outcome["status"],
                outcome=outcome,
                completed_at=now_iso(),
                last_error=None,
            )
            self._event({
                "event": "archived",
                "path": str(path),
                "subject": subject,
                "archive_path": str(destination),
                "import_status": outcome["status"],
            })
        except QualityGateError as exc:
            record.update(status="quality_blocked", last_error=str(exc), failed_at=now_iso())
            self._event({"event": "quality_blocked", "path": str(path), "subject": subject, "error": str(exc)})
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            attempts = int(record.get("attempts", 0))
            max_attempts = max(1, int(self.config.get("max_import_attempts", 3)))
            if attempts < max_attempts:
                retry_delay = max(5, int(self.config.get("retry_delay_seconds", 300)))
                status = "retry_wait"
                record["retry_not_before"] = time.time() + retry_delay
            else:
                status = "import_failed"
            record.update(status=status, last_error=str(exc), failed_at=now_iso())
            self._event({"event": status, "path": str(path), "subject": subject, "error": str(exc)})

    def scan_once(self, include_existing: bool = False) -> dict[str, Any]:
        files = self._candidate_files()
        records: dict[str, dict[str, Any]] = self.state.setdefault("files", {})
        if not self.state.get("initialized") and not include_existing:
            for path in files:
                records[str(path.resolve())] = {
                    "status": "baseline",
                    "signature": file_signature(path),
                    "stable_checks": 0,
                    "first_seen": now_iso(),
                }
            self.state["initialized"] = True
            self._save()
            self._event({"event": "baseline", "files": len(files)})
            return {"baseline": len(files), "processed": 0, "archived": 0}

        self.state["initialized"] = True
        processed = archived = 0
        stable_required = max(1, int(self.config.get("stable_checks", 3)))
        minimum_age = max(0, int(self.config.get("minimum_age_seconds", 20)))
        terminal = {
            "baseline",
            "archived",
            "classification_required",
            "manual_format_required",
            "quality_blocked",
            "import_failed",
        }
        for path in files:
            key = str(path.resolve())
            signature = file_signature(path)
            record = records.get(key)
            if record is None:
                record = records[key] = {
                    "status": "observed", "signature": signature,
                    "stable_checks": 0, "first_seen": now_iso(),
                }
            elif record.get("status") == "baseline" and include_existing:
                record.update(status="observed", stable_checks=0)
            elif record.get("signature") != signature:
                record.update(
                    status="observed", signature=signature, stable_checks=0,
                    first_seen=now_iso(), last_error=None,
                )
            elif record.get("status") in terminal:
                continue
            elif record.get("status") == "retry_wait":
                if time.time() < float(record.get("retry_not_before", 0)):
                    continue
                record.update(status="observed", stable_checks=stable_required)
            else:
                record["stable_checks"] = int(record.get("stable_checks", 0)) + 1
            age = time.time() - path.stat().st_mtime
            if int(record.get("stable_checks", 0)) < stable_required or age < minimum_age:
                continue
            processed += 1
            self._process(path, record)
            if record.get("status") == "archived":
                archived += 1
        self._save()
        return {
            "observed": len(files),
            "processed": processed,
            "archived": archived,
            "status_counts": self.status_summary()["status_counts"],
        }

    def status_summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for record in self.state.get("files", {}).values():
            status = str(record.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        return {
            "initialized": bool(self.state.get("initialized")),
            "watch_dir": str(self.watch_dir.resolve()),
            "archive_root": str(self.archive_root.resolve()),
            "status_counts": counts,
            "state_file": str(self.state_path.resolve()),
            "event_log": str(self.log_path.resolve()),
        }


def load_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    for key in ("watch_dir", "archive_root", "state_file", "event_log", "license", "projects"):
        if key not in config:
            raise ValueError(f"missing config field: {key}")
    return config


def doctor(config: dict[str, Any]) -> dict[str, Any]:
    python_value = Path(config.get("python") or sys.executable)
    projects = {}
    for subject, entry in config["projects"].items():
        root = Path(entry["root"])
        required = [root / entry["notebook"]]
        required.append(root / (entry.get("docx_importer") or entry.get("docx_converter")))
        projects[subject] = {
            "root": str(root),
            "exists": root.is_dir(),
            "commands_exist": all(path.is_file() for path in required),
        }
    archive_root = Path(config["archive_root"])
    return {
        "status": "ok" if (
            Path(config["watch_dir"]).is_dir()
            and python_value.is_file()
            and archive_root.anchor
            and Path(archive_root.anchor).exists()
            and all(item["exists"] and item["commands_exist"] for item in projects.values())
        ) else "error",
        "watch_dir": str(Path(config["watch_dir"])),
        "archive_root": str(archive_root),
        "python": str(python_value),
        "projects": projects,
        "automatic_formats": sorted(SUPPORTED_IMPORT_EXTENSIONS),
        "manual_formats": sorted(OBSERVED_EXTENSIONS - SUPPORTED_IMPORT_EXTENSIONS),
    }


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    completed = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return bool(re.search(rf'(^|,")?{pid}("|,|$)', completed.stdout))


def service_paths(config: dict[str, Any]) -> tuple[Path, Path, Path]:
    state_dir = Path(config["state_file"]).parent
    return state_dir / "service.pid", state_dir / "service.lock", state_dir / "service.log"


def run_service(config_path: Path, include_existing: bool, once: bool) -> int:
    config = load_config(config_path)
    watcher = ExamIngestWatcher(config)
    watcher.archive_root.mkdir(parents=True, exist_ok=True)
    pid_path, lock_path, _ = service_paths(config)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            existing_pid = int(lock_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            existing_pid = 0
        if pid_is_running(existing_pid) and existing_pid != os.getpid():
            raise RuntimeError(f"watcher is already running with PID {existing_pid}")
        lock_path.unlink(missing_ok=True)
    lock_path.write_text(str(os.getpid()), encoding="ascii")
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    try:
        if once:
            print(json.dumps(watcher.scan_once(include_existing), ensure_ascii=False, separators=(",", ":")))
            return 0
        first = True
        while True:
            result = watcher.scan_once(include_existing if first else False)
            first = False
            print(json.dumps({"at": now_iso(), **result}, ensure_ascii=False, separators=(",", ":")), flush=True)
            time.sleep(max(2, int(config.get("poll_seconds", 15))))
    finally:
        if lock_path.is_file() and lock_path.read_text(encoding="ascii").strip() == str(os.getpid()):
            lock_path.unlink(missing_ok=True)
        if pid_path.is_file() and pid_path.read_text(encoding="ascii").strip() == str(os.getpid()):
            pid_path.unlink(missing_ok=True)


def start_service(config_path: Path, include_existing: bool) -> dict[str, Any]:
    config = load_config(config_path)
    check = doctor(config)
    if check["status"] != "ok":
        raise RuntimeError("watcher doctor check failed: " + json.dumps(check, ensure_ascii=False))
    pid_path, _, log_path = service_paths(config)
    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            pid = 0
        if pid_is_running(pid):
            return {"status": "already_running", "pid": pid, "log": str(log_path.resolve())}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        flags |= int(getattr(subprocess, name, 0))
    service_python = str(Path(config.get("python") or sys.executable))
    command = [service_python, "-X", "utf8", "-B", str(Path(__file__).resolve()), "--config", str(config_path.resolve()), "run"]
    if include_existing:
        command.append("--include-existing")
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            close_fds=True, creationflags=flags,
        )
    pid_path.write_text(str(process.pid), encoding="ascii")
    return {"status": "started", "pid": process.pid, "log": str(log_path.resolve()), "baseline_existing": not include_existing}


def stop_service(config: dict[str, Any]) -> dict[str, Any]:
    pid_path, lock_path, _ = service_paths(config)
    if not pid_path.is_file():
        return {"status": "not_running"}
    try:
        pid = int(pid_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        pid = 0
    if pid_is_running(pid):
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr or completed.stdout)
    pid_path.unlink(missing_ok=True)
    lock_path.unlink(missing_ok=True)
    return {"status": "stopped", "pid": pid}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Cross-subject exam download watcher")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")
    p = sub.add_parser("run")
    p.add_argument("--include-existing", action="store_true")
    p.add_argument("--once", action="store_true")
    p = sub.add_parser("start")
    p.add_argument("--include-existing", action="store_true")
    sub.add_parser("stop")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "doctor":
            payload = doctor(config)
        elif args.command == "run":
            return run_service(args.config, args.include_existing, args.once)
        elif args.command == "start":
            payload = start_service(args.config, args.include_existing)
        elif args.command == "stop":
            payload = stop_service(config)
        else:
            watcher = ExamIngestWatcher(config)
            pid_path, _, log_path = service_paths(config)
            pid = int(pid_path.read_text(encoding="ascii")) if pid_path.is_file() else 0
            payload = {
                "service": "running" if pid_is_running(pid) else "stopped",
                "pid": pid or None,
                "log": str(log_path.resolve()),
                **watcher.status_summary(),
            }
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return 0 if payload.get("status", "ok") != "error" else 1
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
