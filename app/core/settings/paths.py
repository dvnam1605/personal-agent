"""Project-root path resolution for settings that name files on disk."""

from pathlib import Path

# Repository root (app/core/settings/paths.py -> settings -> core -> app -> root).
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def resolve_project_path(value: str | Path) -> Path:
    """Resolve a configured path relative to the project root, not the process CWD."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path
