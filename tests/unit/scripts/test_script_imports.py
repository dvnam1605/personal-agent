"""CI import verification for repository scripts."""

import importlib.util
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"


@pytest.mark.parametrize(
    "script_name",
    [
        "ingest_corpus.py",
        "ocr_batch.py",
    ],
)
def test_script_imports_without_error(script_name: str) -> None:
    script_path = SCRIPTS_DIR / script_name
    assert script_path.exists(), f"Script not found: {script_path}"

    spec = importlib.util.spec_from_file_location(f"scripts.{script_path.stem}", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    assert module is not None
