from __future__ import annotations

import argparse
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / ".agents" / "skills" / "math-error-notebook"
HARNESS = ROOT / "skill-packages" / "math-error-notebook-harness"
VARIANT_FILES = {Path("SKILL.md"), Path("agents/openai.yaml")}
HARNESS_ONLY = {
    Path("scripts/deepseek_worker.py"),
    Path("scripts/safe_init.py"),
    Path("scripts/requirements-deepseek.txt"),
}


def files_under(directory: Path) -> set[Path]:
    return {
        path.relative_to(directory)
        for path in directory.rglob("*")
        if path.is_file()
    }


def shared_files() -> set[Path]:
    return files_under(CORE) - VARIANT_FILES


def check() -> list[str]:
    problems: list[str] = []
    core_files = files_under(CORE)
    harness_files = files_under(HARNESS)
    for relative in sorted(HARNESS_ONLY & core_files):
        problems.append(f"pure_package_contains_harness_file:{relative.as_posix()}")
    for relative in sorted(VARIANT_FILES | HARNESS_ONLY):
        if relative not in harness_files:
            problems.append(f"harness_package_missing:{relative.as_posix()}")
    for relative in sorted(shared_files()):
        target = HARNESS / relative
        if not target.is_file():
            problems.append(f"harness_package_missing_shared:{relative.as_posix()}")
        elif (CORE / relative).read_bytes() != target.read_bytes():
            problems.append(f"shared_file_differs:{relative.as_posix()}")
    expected = shared_files() | VARIANT_FILES | HARNESS_ONLY
    for relative in sorted(harness_files - expected):
        problems.append(f"unexpected_harness_file:{relative.as_posix()}")
    return problems


def sync() -> None:
    for relative in sorted(shared_files()):
        target = HARNESS / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CORE / relative, target)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Keep shared files identical across the two Skill packages."
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Copy shared files from the pure Codex package into the Harness package.",
    )
    args = parser.parse_args()
    if args.sync:
        sync()
    problems = check()
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print("skill_packages_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
