"""PHASE3-ADDENDUM.md §0: the rival squad data under tests/fixtures/ is a frozen
snapshot for unit tests only — production always pulls rival squads live from
the API. This test enforces that structurally: nothing outside tests/ may
import tests/fixtures, so a fixture drifting out of date can never silently
become a data source.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKIP_DIRS = {".venv", "venv", ".git", "__pycache__", "node_modules"}


def _production_python_files() -> list[Path]:
    files = []
    for path in _REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(_REPO_ROOT)
        if rel.parts[0] == "tests":
            continue
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        files.append(path)
    return files


def test_no_production_module_imports_tests_fixtures():
    offenders = []
    for path in _production_python_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "tests.fixtures" in text or "tests/fixtures" in text:
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert offenders == [], (
        f"Production code must never import tests/fixtures (PHASE3-ADDENDUM.md §0): {offenders}"
    )
