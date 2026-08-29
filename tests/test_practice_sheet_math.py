from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".agents" / "skills" / "math-error-notebook" / "scripts" / "practice_sheet.py"
SPEC = importlib.util.spec_from_file_location("practice_sheet_math_test", SCRIPT)
assert SPEC and SPEC.loader
practice_sheet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(practice_sheet)


class PracticeSheetMathTests(unittest.TestCase):
    def test_unicode_inner_product_brackets_render_as_math(self) -> None:
        source = r"已知$\vec{e_{1}},\vec{e_{2}}$且$\left⟨\vec{e_{1}},\vec{e_{2}}\right⟩=\frac{\pi}{3}$。"
        html = practice_sheet.paragraph_text(source)

        self.assertEqual(html.count("<img src="), 2)
        self.assertNotIn("vec(", html)
        self.assertNotIn("(π)/(3)", html)
        self.assertIn(r"\langle", practice_sheet._normalize_render_latex(r"\left⟨a,b\right⟩"))


if __name__ == "__main__":
    unittest.main()
