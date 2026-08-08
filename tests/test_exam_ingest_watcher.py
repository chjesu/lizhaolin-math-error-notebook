import importlib.util
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "services" / "exam_ingest_watcher.py"
SPEC = importlib.util.spec_from_file_location("exam_ingest_watcher", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_docx(path: Path, text: str) -> None:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document.encode("utf-8"))


class ExamIngestWatcherTests(unittest.TestCase):
    def make_config(self, root: Path) -> dict:
        return {
            "watch_dir": str(root / "Downloads"),
            "archive_root": str(root / "Archive"),
            "state_file": str(root / "runtime" / "state.json"),
            "event_log": str(root / "runtime" / "events.jsonl"),
            "license": "User-Provided-Authorized",
            "stable_checks": 1,
            "minimum_age_seconds": 0,
            "max_import_attempts": 1,
            "retry_delay_seconds": 5,
            "projects": {},
        }

    def test_classifies_filename_and_docx_body(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            named = root / "高二物理期末试卷.docx"
            write_docx(named, "期末考试")
            body_only = root / "期末测试.docx"
            write_docx(body_only, "高一化学试题 本试卷共20题")

            self.assertEqual(MODULE.classify_subject(named)[0], "physics")
            self.assertEqual(MODULE.classify_subject(body_only)[0], "chemistry")

    def test_pid_detection_recognizes_current_process(self):
        self.assertTrue(MODULE.pid_is_running(os.getpid()))
        self.assertFalse(MODULE.pid_is_running(2_147_483_647))

    def test_once_scan_refuses_to_race_a_running_service(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.make_config(root)
            Path(config["watch_dir"]).mkdir()
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            _, lock_path, _ = MODULE.service_paths(config)
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text(str(os.getpid()), encoding="ascii")

            with self.assertRaisesRegex(RuntimeError, "one-off scan would race"):
                MODULE.run_service(config_path, include_existing=True, once=True)

    def test_successful_import_is_archived(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.make_config(root)
            downloads = Path(config["watch_dir"])
            downloads.mkdir()
            source = downloads / "高二数学期末试卷.docx"
            write_docx(source, "数学试题")
            calls = []

            def importer(subject, path, digest):
                calls.append((subject, path.name, digest))
                return {"status": "imported", "questions": 20}

            watcher = MODULE.ExamIngestWatcher(config, importer=importer)
            watcher.scan_once(include_existing=True)
            result = watcher.scan_once(include_existing=True)

            self.assertEqual(result["archived"], 1)
            self.assertFalse(source.exists())
            archived = list(Path(config["archive_root"]).rglob("*.docx"))
            self.assertEqual(len(archived), 1)
            self.assertEqual(calls[0][0], "math")
            event = json.loads(Path(config["event_log"]).read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(event["event"], "archived")

    def test_failed_import_stays_in_downloads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.make_config(root)
            downloads = Path(config["watch_dir"])
            downloads.mkdir()
            source = downloads / "高二化学期末试卷.docx"
            write_docx(source, "化学试题")

            def importer(subject, path, digest):
                raise RuntimeError("quality gate blocked")

            watcher = MODULE.ExamIngestWatcher(config, importer=importer)
            watcher.scan_once(include_existing=True)
            watcher.scan_once(include_existing=True)

            self.assertTrue(source.exists())
            self.assertFalse(Path(config["archive_root"]).exists())
            record = next(iter(watcher.state["files"].values()))
            self.assertEqual(record["status"], "import_failed")

    def test_quality_gate_failure_is_not_retried_or_archived(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.make_config(root)
            config["max_import_attempts"] = 3
            downloads = Path(config["watch_dir"])
            downloads.mkdir()
            source = downloads / "高二物理期末试卷.docx"
            write_docx(source, "物理试题")
            calls = []

            def importer(subject, path, digest):
                calls.append(path)
                raise MODULE.QualityGateError("question 20 is truncated")

            watcher = MODULE.ExamIngestWatcher(config, importer=importer)
            watcher.scan_once(include_existing=True)
            watcher.scan_once(include_existing=True)
            watcher.scan_once(include_existing=True)

            self.assertTrue(source.exists())
            self.assertEqual(len(calls), 1)
            record = next(iter(watcher.state["files"].values()))
            self.assertEqual(record["status"], "quality_blocked")

    def test_explicit_retry_requeues_a_quality_blocked_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.make_config(root)
            downloads = Path(config["watch_dir"])
            downloads.mkdir()
            source = downloads / "高二数学期末试卷.docx"
            write_docx(source, "数学试题")
            watcher = MODULE.ExamIngestWatcher(
                config,
                importer=lambda *_: (_ for _ in ()).throw(
                    MODULE.QualityGateError("option labels need parser repair")
                ),
            )
            watcher.scan_once(include_existing=True)
            watcher.scan_once(include_existing=True)

            result = watcher.retry_files([source])
            record = next(iter(watcher.state["files"].values()))

            self.assertEqual(result["retried"], [str(source.resolve())])
            self.assertEqual(record["status"], "observed")
            self.assertEqual(record["attempts"], 0)

    def test_retry_command_refuses_to_race_running_service(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.make_config(root)
            downloads = Path(config["watch_dir"])
            downloads.mkdir()
            source = downloads / "高二化学期末试卷.docx"
            write_docx(source, "化学试题")
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            pid_path, lock_path, _ = MODULE.service_paths(config)
            pid_path.parent.mkdir(parents=True)
            lock_path.write_text(str(os.getpid()), encoding="ascii")

            result = MODULE.main([
                "--config", str(config_path), "retry", str(source),
            ])

            self.assertEqual(result, 1)
            self.assertFalse(Path(config["state_file"]).exists())

    def test_first_start_baselines_existing_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.make_config(root)
            downloads = Path(config["watch_dir"])
            downloads.mkdir()
            source = downloads / "高二数学期末试卷.docx"
            write_docx(source, "数学试题")
            calls = []
            watcher = MODULE.ExamIngestWatcher(
                config,
                importer=lambda *args: calls.append(args) or {"status": "imported"},
            )

            result = watcher.scan_once(include_existing=False)
            watcher.scan_once(include_existing=False)

            self.assertEqual(result["baseline"], 1)
            self.assertEqual(calls, [])
            self.assertTrue(source.exists())

    def test_pdf_is_left_for_manual_conversion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.make_config(root)
            downloads = Path(config["watch_dir"])
            downloads.mkdir()
            source = downloads / "高二物理期末试卷.pdf"
            source.write_bytes(b"%PDF-1.4\n")
            watcher = MODULE.ExamIngestWatcher(
                config,
                importer=lambda *args: self.fail("PDF must not reach the importer"),
            )

            watcher.scan_once(include_existing=True)
            watcher.scan_once(include_existing=True)

            self.assertTrue(source.exists())
            record = next(iter(watcher.state["files"].values()))
            self.assertEqual(record["status"], "manual_format_required")

    def test_staging_adds_subject_prefix_without_renaming_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Downloads" / "期末试卷.docx"
            source.parent.mkdir()
            write_docx(source, "物理试题")
            digest = MODULE.file_sha256(source)

            input_dir = MODULE.ImportOrchestrator._stage_source(source, root, digest, "physics")

            self.assertTrue((input_dir / "物理-期末试卷.docx").is_file())
            self.assertEqual(source.name, "期末试卷.docx")

    def test_chemistry_uses_authoritative_docx_batch_import(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "data" / "auto-ingest" / "abc" / "input"
            input_dir.mkdir(parents=True)
            digest = "a" * 64
            calls = []

            def runner(command, cwd, timeout):
                calls.append((command, cwd, timeout))
                output_root = Path(command[command.index("--output-root") + 1])
                manifest_path = output_root / "import-manifest.json"
                manifest_path.parent.mkdir(parents=True)
                manifest_path.write_text(json.dumps({
                    "schema": "chemistry-docx-import-batch/v1",
                    "do_import": True,
                    "files": [{
                        "status": "imported",
                        "sha256": digest,
                        "quality_gate_result": {"status": "pass"},
                        "import_result": {
                            "transaction": "committed",
                            "expected_records": 19,
                            "accounted_records": 19,
                        },
                    }],
                }), encoding="utf-8")
                payload = {
                    "files": 1,
                    "statuses": {"imported": 1},
                    "imported_sources": 1,
                    "blocked_sources": 0,
                    "skipped_existing_sources": 0,
                    "manifest": str(manifest_path),
                }
                return MODULE.CommandResult(0, payload, json.dumps(payload), "")

            config = self.make_config(root)
            config["command_timeout_seconds"] = 37
            orchestrator = MODULE.ImportOrchestrator(config, runner=runner)
            result = orchestrator._import_chemistry(
                root, root / "notebook.py", input_dir, digest
            )

            command, cwd, timeout = calls[0]
            self.assertEqual(cwd, root)
            self.assertEqual(timeout, 37)
            self.assertIn("import-docx-batch", command)
            self.assertIn("--known-root", command)
            self.assertIn("--rights-confirmed", command)
            self.assertIn("--import", command)
            self.assertEqual(result["status"], "imported")
            self.assertEqual(result["verification_status"], "pending_per_item")

    def test_chemistry_quality_block_is_not_archivable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "input"
            input_dir.mkdir()
            digest = "b" * 64

            def runner(command, cwd, timeout):
                output_root = Path(command[command.index("--output-root") + 1])
                manifest_path = output_root / "import-manifest.json"
                manifest_path.parent.mkdir(parents=True)
                manifest_path.write_text(json.dumps({
                    "schema": "chemistry-docx-import-batch/v1",
                    "do_import": True,
                    "files": [{
                        "status": "blocked_quality_gate",
                        "sha256": digest,
                    }],
                }), encoding="utf-8")
                payload = {
                    "files": 1,
                    "statuses": {"blocked_quality_gate": 1},
                    "imported_sources": 0,
                    "blocked_sources": 1,
                    "skipped_existing_sources": 0,
                    "manifest": str(manifest_path),
                }
                return MODULE.CommandResult(0, payload, json.dumps(payload), "")

            orchestrator = MODULE.ImportOrchestrator(self.make_config(root), runner=runner)
            with self.assertRaisesRegex(MODULE.QualityGateError, "chemistry authoritative import"):
                orchestrator._import_chemistry(
                    root, root / "notebook.py", input_dir, digest
                )

    def test_import_routing_changes_only_chemistry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "Downloads" / "试卷.docx"
            source.parent.mkdir()
            write_docx(source, "试题")
            digest = MODULE.file_sha256(source)
            config = self.make_config(root)
            config["projects"] = {
                subject: {
                    "root": str(root / subject),
                    "notebook": "notebook.py",
                    "docx_converter": "converter.py",
                }
                for subject in ("math", "physics", "chemistry")
            }
            orchestrator = MODULE.ImportOrchestrator(config)

            with (
                patch.object(orchestrator, "_stage_source", return_value=root / "input"),
                patch.object(orchestrator, "_verify_bank", return_value={"integrity_check": "ok"}),
                patch.object(orchestrator, "_import_math", return_value={"status": "imported"}) as math_import,
                patch.object(orchestrator, "_import_science", return_value={"status": "imported"}) as science_import,
                patch.object(orchestrator, "_import_chemistry", return_value={"status": "imported"}) as chemistry_import,
            ):
                orchestrator.import_file("chemistry", source, digest)
                chemistry_import.assert_called_once()
                math_import.assert_not_called()
                science_import.assert_not_called()

                chemistry_import.reset_mock()
                orchestrator.import_file("physics", source, digest)
                science_import.assert_called_once()
                chemistry_import.assert_not_called()
                math_import.assert_not_called()


if __name__ == "__main__":
    unittest.main()
