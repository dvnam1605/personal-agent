"""Repository paths that stay correct after tests are nested under purpose folders."""

from pathlib import Path


def repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "app").is_dir():
            return parent
    raise RuntimeError("repository root not found")


def tests_root() -> Path:
    return repo_root() / "tests"


def fixtures_root() -> Path:
    return tests_root() / "fixtures"
