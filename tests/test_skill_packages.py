from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
PURE = ROOT / ".agents/skills/math-error-notebook"
HARNESS = ROOT / "skill-packages/math-error-notebook-harness"


class SkillPackageTests(unittest.TestCase):
    def test_packages_have_expected_harness_files(self):
        harness_only = (
            "scripts/deepseek_worker.py",
            "scripts/safe_init.py",
            "scripts/requirements-deepseek.txt",
        )
        for relative in harness_only:
            self.assertFalse((PURE / relative).exists())
            self.assertTrue((HARNESS / relative).is_file())

    def test_shared_package_files_are_identical(self):
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "scripts/sync_skill_packages.py"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
