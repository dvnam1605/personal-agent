"""Document images: content-addressed files and their references in chunk text.

An image extracted at ingestion is stored once as ``<key>.<ext>`` (key = the
first 24 hex digits of its SHA-256) and appears in chunk text as Markdown,
``![Hình 4 — mô tả tự động: …](image:<key>)``, at its place in the reading
order. The model only ever sees the alt text; answers and reports put the
images back next to the paragraphs that cite their evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

IMAGE_REF = re.compile(r"!\[(?P<alt>[^\]\n]*)\]\(image:(?P<key>[0-9a-f]{24})\)")
IMAGE_LINE = re.compile(r"^\s*!\[[^\]\n]*\]\(image:[0-9a-f]{24}\)\s*$")
KEY_PATTERN = re.compile(r"^[0-9a-f]{24}$")
MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def images_dir() -> Path:
    return Path(os.getenv("DOC_IMAGES_DIR", "storage/doc_images"))


def image_key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:24]


def image_ref(alt: str, key: str) -> str:
    clean = re.sub(r"\s+", " ", alt.replace("[", "(").replace("]", ")")).strip()
    return f"![{clean}](image:{key})"


def image_file(key: str) -> Path | None:
    if not KEY_PATTERN.match(key):
        return None
    for ext in MEDIA_TYPES:
        path = images_dir() / f"{key}.{ext}"
        if path.exists():
            return path
    return None


def save_image(key: str, data: bytes, ext: str) -> Path:
    path = images_dir() / f"{key}.{ext}"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path


def cached_description(key: str) -> str | None:
    try:
        return json.loads((images_dir() / f"{key}.json").read_text(encoding="utf-8"))["text"]
    except (OSError, ValueError, KeyError):
        return None


def cache_description(key: str, text: str) -> None:
    path = images_dir() / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")


def for_prompt(text: str) -> str:
    """Chunk text as the model sees it: an image is its alt text in parentheses."""
    return IMAGE_REF.sub(lambda m: f"({m.group('alt')})", text)


def strip_images(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", IMAGE_REF.sub("", text)).strip()
