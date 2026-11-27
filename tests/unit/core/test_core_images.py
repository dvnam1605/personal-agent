"""Where the document image store lives on disk."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.images import images_dir
from app.core.settings.paths import PROJECT_ROOT


def test_default_images_dir_is_anchored_at_the_project_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DOC_IMAGES_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert images_dir() == PROJECT_ROOT / "storage" / "doc_images"


def test_relative_override_is_anchored_and_absolute_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOC_IMAGES_DIR", "custom/images")
    assert images_dir() == PROJECT_ROOT / "custom" / "images"
    monkeypatch.setenv("DOC_IMAGES_DIR", str(tmp_path))
    assert images_dir() == tmp_path
