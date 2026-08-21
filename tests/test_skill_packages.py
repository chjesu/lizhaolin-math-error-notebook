from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PURE = ROOT / ".agents/skills/math-error-notebook"
LEGACY_SECOND_PACKAGE = ROOT / "skill-packages/math-error-notebook-harness"


class SkillPackageTests(unittest.TestCase):
    def test_single_installable_skill_package(self):
        self.assertTrue((PURE / "SKILL.md").is_file())
        self.assertTrue((PURE / "agents/openai.yaml").is_file())
        self.assertTrue((PURE / "scripts/notebook.py").is_file())
        self.assertFalse(LEGACY_SECOND_PACKAGE.exists())


if __name__ == "__main__":
    unittest.main()
