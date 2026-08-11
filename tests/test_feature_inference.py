import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "skills" / "math-error-notebook" / "scripts" / "notebook.py"
SPEC = importlib.util.spec_from_file_location("math_notebook_feature_inference", SCRIPT)
notebook = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(notebook)


class FeatureInferenceTests(unittest.TestCase):
    def test_common_choice_question_type_names_do_not_become_solutions(self):
        for question_type in ("单选题", "单项选择题", "选择题"):
            with self.subTest(question_type=question_type):
                features = notebook.infer_question_features("已知条件，选择正确结论。", question_type)
                self.assertIn("single-choice", features)
                self.assertNotIn("solution", features)

    def test_multiple_choice_and_fill_blank_keep_their_own_features(self):
        multiple = notebook.infer_question_features("选择所有正确结论。", "多选题")
        blank = notebook.infer_question_features("结果为______。", "填空题")

        self.assertIn("multiple-choice", multiple)
        self.assertNotIn("solution", multiple)
        self.assertIn("fill-blank", blank)
        self.assertNotIn("solution", blank)


if __name__ == "__main__":
    unittest.main()
