import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "build_omml_exam_import.py"
SPEC = importlib.util.spec_from_file_location("build_omml_exam_import", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class OptionParsingTests(unittest.TestCase):
    def test_split_options_preserves_inline_stem_and_mixed_markers(self):
        options, remaining = MODULE.split_options(
            ["1. 已知函数的零点为 A. 1 B．2 C、3 （D）4"]
        )

        self.assertEqual(remaining, ["1. 已知函数的零点为"])
        self.assertEqual([item.split("．", 1)[0] for item in options], list("ABCD"))
        self.assertEqual(options[-1], "D．4")

    def test_isolated_point_label_is_not_treated_as_choice_block(self):
        source = ["1．在三角形 ABC 中，点 A．为顶点"]
        options, remaining = MODULE.split_options(source)

        self.assertEqual(options, [])
        self.assertEqual(remaining, source)


class SegmentationTests(unittest.TestCase):
    @staticmethod
    def records(*lines):
        return [{"index": index, "text": line} for index, line in enumerate(lines)]

    def test_recognizes_common_question_number_styles(self):
        segments, diagnostics = MODULE.analyze_segments(
            self.records("一、选择题", "1. 第一题", "2、第二题", "第3题：第三题")
        )

        self.assertEqual([item["number"] for item in segments], [1, 2, 3])
        self.assertTrue(segments[-1]["lines"][0].startswith("第3题："))
        self.assertEqual(diagnostics["missing_question_numbers"], [])
        self.assertEqual(diagnostics["duplicate_question_numbers"], [])

    def test_reports_gap_and_duplicate_instead_of_silently_importing(self):
        _, diagnostics = MODULE.analyze_segments(
            self.records("1．第一题", "3．第三题", "3. 重复第三题")
        )

        self.assertEqual(diagnostics["missing_question_numbers"], [2])
        self.assertEqual(diagnostics["duplicate_question_numbers"], [3])

    def test_decimal_table_cells_are_not_question_boundaries(self):
        segments, diagnostics = MODULE.analyze_segments(
            self.records("1．统计题", "6.5", "7．5", "2．下一题")
        )

        self.assertEqual([item["number"] for item in segments], [1, 2])
        self.assertEqual(diagnostics["duplicate_question_numbers"], [])

    def test_declared_section_total_exposes_a_truncated_tail(self):
        segments, diagnostics = MODULE.analyze_segments(
            self.records("一、选择题：本题共3小题", "1．第一题", "2．第二题")
        )
        problems = MODULE.build_quality_problems(diagnostics, [], [])

        self.assertEqual(len(segments), 2)
        self.assertEqual(diagnostics["declared_question_count"], 3)
        self.assertIn("declared_question_count_mismatch", problems)


class QuestionParsingTests(unittest.TestCase):
    def test_requires_a_real_solution_marker(self):
        lines = [
            "1．函数 $f(x)=x$ 的值为 A．1 B．2 C．3 D．4",
            "【答案】A",
            "【难度】0.8",
            "【知识点】函数",
        ]

        with self.assertRaisesRegex(ValueError, "missing solution marker"):
            MODULE.parse_question(1, "一、选择题", lines, "batch", "batch", 10, 1, "2025-2026")

    def test_inline_metadata_is_split_and_answer_not_copied_into_solution(self):
        lines = [
            "1. 已知 $1+1=$ A. 2 B. 3 C. 4 D. 5",
            "【答案】A【难度】0.8【知识点】代数式【解析】因为 $1+1=2$，所以选 A。",
        ]

        question = MODULE.parse_question(
            1, "一、选择题", lines, "batch", "batch", 10, 1, "2025-2026"
        )

        self.assertEqual(question["answer"], "A")
        self.assertIn("1+1=2", question["solution"])
        self.assertNotIn("【答案】", question["solution"])
        self.assertTrue(question["stem"].startswith("已知"))
        self.assertEqual(len(question["options"]), 4)

    def test_answer_images_are_localized_too(self):
        lines = [
            "1．请根据图形作答。",
            "【答案】[IMAGE:media/image1.png]",
            "【解析】由答案图中的辅助线即可得到结论。",
        ]

        question = MODULE.parse_question(
            1, "三、解答题", lines, "paper-01", "batch-01", 10, 1, "2025-2026"
        )

        self.assertEqual(
            question["answer"],
            "![原题图](data/imports/batch-01/paper-01/media/image1.png)",
        )

    def test_quality_gate_blocks_skips_and_number_gaps(self):
        diagnostics = {
            "detected_segments": 2,
            "duplicate_question_numbers": [],
            "missing_question_numbers": [2],
        }
        problems = MODULE.build_quality_problems(
            diagnostics,
            [{"id": "Q-1", "stem": "完整题干文本", "answer": "A", "solution": "完整解析文本"}],
            [{"question_number": 3, "reason": "missing answer"}],
        )

        self.assertIn("missing_question_numbers", problems)
        self.assertIn("question_parse_failures", problems)
        self.assertIn("parsed_question_count_mismatch", problems)

    def test_quality_gate_blocks_choice_without_structured_options(self):
        diagnostics = {
            "detected_segments": 1,
            "duplicate_question_numbers": [],
            "missing_question_numbers": [],
        }
        problems = MODULE.build_quality_problems(
            diagnostics,
            [{
                "id": "Q-1",
                "stem": "完整的选择题题干文本",
                "answer": "A",
                "solution": "完整的选择题解析文本",
                "source_section": "一、选择题",
            }],
            [],
        )

        self.assertIn("Q-1:missing_choice_options", problems)

    def test_quality_gate_blocks_missing_localized_image(self):
        diagnostics = {
            "detected_segments": 1,
            "duplicate_question_numbers": [],
            "missing_question_numbers": [],
        }
        problems = MODULE.build_quality_problems(
            diagnostics,
            [{
                "id": "Q-1",
                "stem": "图形题完整题干 ![原题图](missing.png)",
                "answer": "1",
                "solution": "根据图中条件可计算得到答案。",
                "source_section": "三、解答题",
            }],
            [],
            ROOT,
        )

        self.assertIn("Q-1:missing_image:missing.png", problems)


if __name__ == "__main__":
    unittest.main()
