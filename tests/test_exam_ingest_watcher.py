import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
