from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PATHS_SCRIPT = (
    ROOT
    / ".agents"
    / "skills"
    / "math-error-notebook"
    / "scripts"
    / "math_notebook_project_paths.py"
)
SPEC = importlib.util.spec_from_file_location("math_notebook_project_paths", PATHS_SCRIPT)
project_paths = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(project_paths)


class InstalledSkillPathTests(unittest.TestCase):
    def test_project_local_skill_uses_own_project(self):
        skill = ROOT / ".agents" / "skills" / "math-error-notebook"
        self.assertEqual(project_paths.resolve_project_root(skill), ROOT)

    def test_installed_skill_finds_nearest_notebook_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            skill = base / "home" / ".codex" / "skills" / "math-error-notebook"
            nested = base / "project" / "work" / "child"
            database = base / "project" / "data" / "math_notebook.db"
            skill.mkdir(parents=True)
            nested.mkdir(parents=True)
            database.parent.mkdir(parents=True)
            database.touch()
            self.assertEqual(
                project_paths.resolve_project_root(skill, nested),
                (base / "project").resolve(),
            )

    def test_environment_binding_wins_for_an_installed_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            configured = base / "configured-project"
            skill = base / ".codex" / "skills" / "math-error-notebook"
            with patch.dict(
                os.environ,
                {project_paths.PROJECT_ROOT_ENV: str(configured)},
                clear=False,
            ):
                self.assertEqual(
                    project_paths.resolve_project_root(skill, base), configured.resolve()
                )


if __name__ == "__main__":
    unittest.main()
