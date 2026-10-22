"""Store a parsed document's images and describe them with a vision model.

Screenshots in a how-to guide carry most of the content ("Tiếp tục chọn
Accept." under a picture of the dialog), so each distinct image gets one
short description, written from the image and the text around it. The
description becomes the image's alt text, which is what retrieval matches
and what the answering model reads. Descriptions are cached next to the
image file, so a re-ingest or a repeated image costs no call; a failed call
leaves the image with its plain "Hình N" label.
"""

from __future__ import annotations

import asyncio
import logging
import re

from app.core.images import (
    IMAGE_REF,
    MEDIA_TYPES,
    cache_description,
    cached_description,
    image_ref,
    save_image,
    strip_images,
)
from app.core.llm import llm_describe_image
from app.domain.models.ingestion.parsed_document import ParsedDocument

logger = logging.getLogger(__name__)

CONCURRENCY = 3
TIMEOUT_SECONDS = 60.0
CONTEXT_CHARS = 500
MAX_DESCRIPTION_CHARS = 600

_SYSTEM = (
    "Bạn mô tả một hình trong tài liệu (thường là ảnh chụp màn hình của hướng dẫn) bằng tiếng "
    "Việt, 1–3 câu: đây là cửa sổ/ứng dụng/sơ đồ gì, đang ở bước nào, nút hoặc mục cần bấm "
    "(chép nguyên văn chữ trên hình, kể cả chỗ được khoanh hoặc đánh dấu). Chỉ mô tả những gì "
    "nhìn thấy trong hình; không suy đoán, không nhắc lại đoạn văn nếu hình không thể hiện."
)


def store_images(document: ParsedDocument) -> None:
    for image in document.images:
        save_image(image.key, image.data, image.ext)


# A figure's own caption: "Hình 4.3: Kiến trúc tổng thể", "Hình 2. Sơ đồ …".
_CAPTION = re.compile(r"^\s*(?P<label>Hình\s*\d+(?:\.\d+)*)\s*[:.\-–]?\s*(?P<title>.*)$", re.I)
CAPTION_TITLE_CHARS = 150


def adopt_captions(document: ParsedDocument) -> ParsedDocument:
    """Label each image with the source's own caption next to it ("Hình 4.3: …").

    The text then says "như Hình 4.3" and the image carries the same label, so
    the model can point at it; images without a caption keep "Hình N".
    """
    nodes = list(document.nodes)
    changed = False
    for index, node in enumerate(nodes):
        ref = IMAGE_REF.fullmatch(node.text.strip())
        if ref is None:
            continue
        for near in (index + 1, index - 1):
            if not 0 <= near < len(nodes) or IMAGE_REF.search(nodes[near].text):
                continue
            caption = _CAPTION.match(nodes[near].text.split("\n", 1)[0])
            if caption is None:
                continue
            label = re.sub(r"\s+", " ", caption.group("label")).strip()
            title = caption.group("title").strip()[:CAPTION_TITLE_CHARS]
            alt = f"{label}: {title}" if title else label
            nodes[index] = node.model_copy(update={"text": image_ref(alt, ref.group("key"))})
            changed = True
            break
    return document.model_copy(update={"nodes": tuple(nodes)}) if changed else document


class ImageDescriber:
    """Fills each image reference's alt text with a short description."""

    async def adescribe(self, document: ParsedDocument) -> ParsedDocument:
        if not document.images:
            return document
        nodes = list(document.nodes)
        first_use: dict[str, int] = {}
        for index, node in enumerate(nodes):
            for m in IMAGE_REF.finditer(node.text):
                first_use.setdefault(m.group("key"), index)
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def describe(key: str) -> tuple[str, str | None]:
            cached = cached_description(key)
            if cached:
                return key, cached
            image = next(i for i in document.images if i.key == key)
            index = first_use.get(key, 0)
            before = " ".join(strip_images(n.text) for n in nodes[max(0, index - 3) : index])
            after = " ".join(strip_images(n.text) for n in nodes[index + 1 : index + 2])
            async with semaphore:
                text = await llm_describe_image(
                    _SYSTEM,
                    f"Đoạn văn trước hình: {before[-CONTEXT_CHARS:]}\n"
                    f"Đoạn văn sau hình: {after[: CONTEXT_CHARS // 2]}",
                    image.data,
                    MEDIA_TYPES[image.ext],
                    timeout=TIMEOUT_SECONDS,
                )
            text = " ".join((text or "").split())[:MAX_DESCRIPTION_CHARS]
            if text:
                cache_description(key, text)
            return key, text or None

        descriptions = dict(await asyncio.gather(*(describe(k) for k in first_use)))

        def relabel(m: re.Match[str]) -> str:
            text = descriptions.get(m.group("key"))
            label = m.group("alt")
            return (
                image_ref(f"{label} — mô tả tự động: {text}", m.group("key"))
                if text
                else m.group(0)
            )

        for index, node in enumerate(nodes):
            if IMAGE_REF.search(node.text):
                nodes[index] = node.model_copy(update={"text": IMAGE_REF.sub(relabel, node.text)})
        logger.info(
            "images_described total=%d described=%d",
            len(first_use),
            sum(1 for v in descriptions.values() if v),
        )
        return document.model_copy(update={"nodes": tuple(nodes)})
