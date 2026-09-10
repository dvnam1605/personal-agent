"""Import-order guards for the tools ↔ services cycle (N1)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_spill_tools_imports_in_fresh_interpreter() -> None:
    """``import app.tools.spill_tools`` must not fail with a partial CALENDAR_SCOPE cycle."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.tools.spill_tools import SPILL_SLICE_TOOL; "
            "from app.integrations.google_calendar import CALENDAR_SCOPE; "
            "assert CALENDAR_SCOPE",
        ],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_services_package_all_matches_lazy_exports() -> None:
    import app.services as services

    assert set(services.__all__) == set(services._EXPORTS)


def test_app_does_not_import_tests_package() -> None:
    """Production code must not depend on tests.* (M1)."""
    import ast

    offenders: list[str] = []
    for py_path in (REPO / "app").rglob("*.py"):
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "tests" or alias.name.startswith("tests."):
                        offenders.append(f"{py_path.as_posix()}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "tests" or mod.startswith("tests."):
                    offenders.append(f"{py_path.as_posix()}: from {mod}")
    assert offenders == [], f"app/ imports tests: {offenders}"
