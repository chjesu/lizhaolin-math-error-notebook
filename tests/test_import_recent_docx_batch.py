import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_recent_docx_batch.py"
SPEC = importlib.util.spec_from_file_location("import_recent_docx_batch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ImportRecentDocxBatchInferenceTests(unittest.TestCase):
    def test_docx_content_hash_ignores_package_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.docx"
            second = root / "second.docx"
            for path, metadata in ((first, b"created-first"), (second, b"created-second")):
                with zipfile.ZipFile(path, "w") as archive:
                    archive.writestr("word/document.xml", b"<document>same paper</document>")
                    archive.writestr("word/media/image1.png", b"same image")
                    archive.writestr("docProps/core.xml", metadata)

            self.assertNotEqual(MODULE.sha256(first), MODULE.sha256(second))
            self.assertEqual(
                MODULE.docx_content_sha256(first),
                MODULE.docx_content_sha256(second),
            )

    def test_infer_grade_from_gaokao_name(self):
        self.assertEqual(MODULE.infer_grade("2026年高考北京卷数学真题"), 12)
        self.assertEqual(MODULE.infer_grade("2026年上海春季高考数学试卷"), 12)
        self.assertEqual(MODULE.infer_grade("2024年新课标全国Ⅰ卷数学真题"), 12)
        self.assertEqual(MODULE.infer_grade("2024年新课标全国Ⅱ卷数学真题"), 12)

    def test_explicit_school_grade_still_wins(self):
        self.assertEqual(MODULE.infer_grade("某校高一数学试卷"), 10)
        self.assertEqual(MODULE.infer_grade("某校高二数学试卷"), 11)
        self.assertEqual(MODULE.infer_grade("某校高三数学试卷"), 12)

    def test_infer_year_from_gaokao_name(self):
        self.assertEqual(MODULE.infer_year("2024年新课标全国Ⅰ卷数学真题"), "2024")
        self.assertEqual(MODULE.infer_year("2025年高考北京卷数学真题"), "2025")


if __name__ == "__main__":
    unittest.main()
