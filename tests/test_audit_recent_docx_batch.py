import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "audit_recent_docx_batch.py"
SPEC = importlib.util.spec_from_file_location("audit_recent_docx_batch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class AuditRecentDocxBatchTests(unittest.TestCase):
    def test_recovery_manifest_can_include_existing_sources_explicitly(self):
        files = [
            {
                "status": "imported",
                "source_name": "new source",
                "import_result": {"inserted": 20},
            },
            {
                "status": "skipped_existing_source",
                "source_name": "recovered source",
                "existing_questions": 18,
            },
        ]

        self.assertEqual(MODULE.audit_sources(files), ["new source"])
        self.assertEqual(
            MODULE.audit_sources(files, include_existing=True),
            ["new source", "recovered source"],
        )

    def test_image_check_covers_options_and_answer(self):
        missing = "definitely-missing-import-image.png"
        problems = MODULE.image_problems({
            "stem": "题干",
            "stored_answer": f"见图 ![答案]({missing})",
            "stored_solution": "解析",
            "options": [f"A．![选项图]({missing})", "B．普通选项"],
        })

        self.assertIn(f"missing_image:{missing}", problems)

    def test_diagram_reference_is_a_blocking_issue(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            packet = {
                "question": {
                    "stem": f"如图所示，求该图形的面积。![图]({image.name})",
                    "stored_answer": "1",
                    "stored_solution": "由图中条件计算可得面积为 1。",
                    "question_type": "解答题",
                    "knowledge_codes": ["geometry"],
                    "target_causes": ["concept-not-mastered"],
                    "feature_codes": ["diagram"],
                    "source_record": {"rights_confirmed": True, "license": "user-provided"},
                },
                "automatic_checks": {"issues": ["diagram_reference"]},
                "source_record": {"rights_confirmed": True, "license": "user-provided"},
            }

            self.assertIn("diagram_reference", MODULE.structural_problems(packet))


if __name__ == "__main__":
    unittest.main()
