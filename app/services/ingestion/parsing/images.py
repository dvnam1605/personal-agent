"""Collect a PDF's images while parsing, as references in reading order.

Each kept image becomes a paragraph ``![Hình N](image:<key>)`` where it
appears; identical images (a dialog shown in two steps) share one key.
Icons, bullets and rules are skipped by size.
"""

from __future__ import annotations

from typing import Any

from app.core.images import image_key, image_ref
from app.domain.models.ingestion.parsed_document import ParsedImage

MIN_SIDE = 48
MIN_AREA = 12_000
_KEPT_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


def _as_web_image(data: bytes, ext: str) -> tuple[bytes, str] | None:
    """The image as PNG/JPEG/GIF/WebP (JPX, JBIG2, CMYK … are converted to PNG)."""
    ext = ext.lower()
    if ext in _KEPT_EXTS:
        return data, ext
    try:
        import pymupdf

        pix = pymupdf.Pixmap(data)
        if pix.n - pix.alpha >= 4:  # CMYK
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        return pix.tobytes("png"), "png"
    except Exception:  # noqa: BLE001 - an unreadable image is skipped
        return None


class ImageCollector:
    def __init__(self) -> None:
        self._images: dict[str, ParsedImage] = {}
        self._count = 0

    @property
    def images(self) -> tuple[ParsedImage, ...]:
        return tuple(self._images.values())

    def add(
        self,
        data: bytes,
        ext: str,
        width: int,
        height: int,
        page: int | None,
        caption: str = "",
    ) -> str | None:
        """Reference text for the image, or None when it is too small or unreadable."""
        if width < MIN_SIDE or height < MIN_SIDE or width * height < MIN_AREA:
            return None
        converted = _as_web_image(data, ext)
        if converted is None:
            return None
        data, ext = converted
        key = image_key(data)
        if key not in self._images:
            self._images[key] = ParsedImage(
                key=key, data=data, ext=ext, width=width, height=height, page=page
            )
        self._count += 1
        alt = f"Hình {self._count}: {caption}" if caption else f"Hình {self._count}"
        return image_ref(alt, key)

    def add_region(
        self,
        doc: Any,
        page_no: int,
        rect: tuple[float, float, float, float],
        caption: str = "",
    ) -> str | None:
        """Image shown in a page region: the embedded image covering it, else a render."""
        try:
            page = doc[page_no - 1]
            import pymupdf

            box = pymupdf.Rect(rect)
            for info in page.get_image_info(xrefs=True):
                xref = info.get("xref") or 0
                shown = pymupdf.Rect(info["bbox"])
                if xref and box.get_area() and (shown & box).get_area() >= 0.8 * box.get_area():
                    raw = doc.extract_image(xref)
                    return self.add(
                        raw["image"], raw["ext"], raw["width"], raw["height"], page_no, caption
                    )
            pix = page.get_pixmap(clip=box, dpi=150)
            return self.add(pix.tobytes("png"), "png", pix.width, pix.height, page_no, caption)
        except Exception:  # noqa: BLE001 - images are a best-effort extra
            return None
