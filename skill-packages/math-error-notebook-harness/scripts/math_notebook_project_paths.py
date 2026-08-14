"""Resolve the notebook project used by a project-local or installed skill."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT_ENV = "LIZHAOLIN_MATH_NOTEBOOK_ROOT"


def resolve_project_root(skill_dir: Path, cwd: Path | None = None) -> Path:
    """Return the explicitly bound, project-local, or nearest working project."""
    configured = os.environ.get(PROJECT_ROOT_ENV)
    if configured:
        return Path(configured).expanduser().resolve()

    skill_dir = skill_dir.resolve()
    if skill_dir.parent.name == "skills" and skill_dir.parent.parent.name == ".agents":
        return skill_dir.parents[2]

    current = (cwd or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "data" / "math_notebook.db").is_file():
            return candidate
        if (
            candidate
            / ".agents"
            / "skills"
            / "math-error-notebook"
            / "SKILL.md"
        ).is_file():
            return candidate
    return current
