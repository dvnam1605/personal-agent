"""Scanned PDF -> per-page Markdown with the fine-tuned VL model (the OCR web app's scan path).

Per page:

1. render at 200 dpi (pages with a text layer also at 300 dpi, used for crops: small text on
   a 200 dpi render blurs stacked Vietnamese marks such as Ố Ể Ễ);
2. fix orientation: PP-LCNet page classifier, checked by a text-line vote so a false 180°
   turn is undone;
3. PP-DocLayoutV2 finds blocks and their reading order (:mod:`.layout` cleans the boxes);
4. every block is cropped with a small pad; short text lines are upscaled to 48 px and narrow
   tables up to 2x so the model sees text at the size it was trained on;
5. the VL model reads the crops of up to three pages per GPU round (:mod:`.recognizer`);
6. blocks become Markdown (:mod:`.markdown`), and tables split by a page break are joined.

All Paddle models run on CPU in one long-lived worker thread (Paddle predictors only work in
the thread that created them), so layout of the next pages overlaps GPU decoding.
"""

from __future__ import annotations

import os
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from .layout import IMAGE_LABELS, Block, blocks_from_layout
from .markdown import join_page_tables, page_markdown
from .recognizer import MAX_PIXELS, VlRecognizer

PIPELINE_VERSION = "scan-v1"
DPI = 200
HI_DPI = 300
TEXT_LAYER_MIN_CHARS = 20
CROP_PAD = 4
MIN_TEXT_HEIGHT = 48
TABLE_MIN_WIDTH = 1600
PAGES_PER_ROUND = 3
ORIENTATION_MIN_SCORE = 0.6


@dataclass
class RenderedPage:
    index: int
    png: Path
    hi_png: Path | None = None


@dataclass
class PreparedPage:
    index: int
    blocks: list[Block] = field(default_factory=list)
    crops: list[tuple[Block, Image.Image]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PageText:
    index: int
    markdown: str
    warnings: list[str] = field(default_factory=list)


def render_pages(pdf_path: Path, out_dir: Path) -> list[RenderedPage]:
    """Rasterise every page; a 300 dpi copy only for pages with a text layer."""
    import pymupdf

    pages: list[RenderedPage] = []
    with pymupdf.open(pdf_path) as doc:
        for index, page in enumerate(doc):
            png = out_dir / f"page_{index + 1:04d}.png"
            page.get_pixmap(dpi=DPI).save(png)
            hi_png = None
            if len(str(page.get_text()).strip()) > TEXT_LAYER_MIN_CHARS:
                hi_png = png.with_name(png.stem + "_hi.png")
                page.get_pixmap(dpi=HI_DPI).save(hi_png)
            pages.append(RenderedPage(index, png, hi_png))
    return pages


def _scaled(crop: Image.Image, factor: float) -> Image.Image:
    size = (round(crop.width * factor), round(crop.height * factor))
    return crop.resize(size, Image.Resampling.LANCZOS)


def crop_blocks(
    page: Image.Image, blocks: list[Block], hi_page: Image.Image | None = None
) -> list[tuple[Block, Image.Image]]:
    """Crops to read, one per text/table/formula block; images and slivers get empty text."""
    width, height = page.size
    source, scale = page, 1.0
    if hi_page is not None and abs(hi_page.width / hi_page.height - width / height) < 0.01:
        source, scale = hi_page, hi_page.width / width
    crops: list[tuple[Block, Image.Image]] = []
    for block in blocks:
        x1, y1, x2, y2 = block.box
        box = (
            max(0, int(x1) - CROP_PAD),
            max(0, int(y1) - CROP_PAD),
            min(width, int(x2) + CROP_PAD),
            min(height, int(y2) + CROP_PAD),
        )
        if block.label in IMAGE_LABELS or box[2] - box[0] < 4 or box[3] - box[1] < 4:
            block.text = ""
            continue
        left, top, right, bottom = (int(round(v * scale)) for v in box)
        crop = source.crop((left, top, right, bottom))
        if block.task == "ocr" and crop.height < MIN_TEXT_HEIGHT:
            crop = _scaled(crop, min(2.0, MIN_TEXT_HEIGHT / crop.height))
        elif block.task == "table" and crop.width < TABLE_MIN_WIDTH:
            factor = min(
                2.0, TABLE_MIN_WIDTH / crop.width, (MAX_PIXELS / (crop.width * crop.height)) ** 0.5
            )
            if factor > 1.1:
                crop = _scaled(crop, factor)
        crops.append((block, crop))
    return crops


class PaddleModels:
    """Orientation, text-line and layout models from PaddleOCR, all on CPU, created lazily."""

    def __init__(self) -> None:
        # Skip PaddleX's per-start connectivity probe of model hosts (old and 3.7+ names).
        os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        self._layout: Any = None
        self._orientation: Any = None
        self._line_detector: Any = None
        self._line_orientation: Any = None

    def layout(self, png: Path) -> list[Block]:
        if self._layout is None:
            from paddleocr import LayoutDetection

            self._layout = LayoutDetection(model_name="PP-DocLayoutV2", device="cpu")
        result = next(iter(self._layout.predict(str(png), batch_size=1, layout_nms=True)))
        return blocks_from_layout(result.json)

    def page_orientation(self, png: Path) -> tuple[int, float]:
        if self._orientation is None:
            from paddleocr import DocImgOrientationClassification

            self._orientation = DocImgOrientationClassification(
                model_name="PP-LCNet_x1_0_doc_ori", device="cpu"
            )
        result = next(iter(self._orientation.predict(str(png), batch_size=1))).json["res"]
        return int(result["label_names"][0]), float(result["scores"][0])

    def line_votes(self, image: Image.Image) -> tuple[int, int]:
        """(upright, upside-down) votes over the widest horizontal text lines."""
        import numpy as np
        from paddleocr import TextDetection, TextLineOrientationClassification

        if self._line_detector is None:
            self._line_detector = TextDetection(model_name="PP-OCRv5_mobile_det", device="cpu")
            self._line_orientation = TextLineOrientationClassification(
                model_name="PP-LCNet_x1_0_textline_ori", device="cpu"
            )
        array = np.ascontiguousarray(np.array(image.convert("RGB"))[:, :, ::-1])
        detection = next(iter(self._line_detector.predict(array, batch_size=1)))
        lines = []
        for poly in detection.json["res"]["dt_polys"]:
            points = np.array(poly)
            (x1, y1), (x2, y2) = points.min(0), points.max(0)
            if x2 - x1 >= 2 * (y2 - y1) and y2 - y1 >= 6:
                lines.append(array[int(y1) : int(y2), int(x1) : int(x2)])
        lines = sorted(lines, key=lambda line: -line.shape[1])[:40]
        upright = upside_down = 0
        for result in self._line_orientation.predict(lines, batch_size=16) if lines else []:
            if result.json["res"]["label_names"][0] == "180_degree":
                upside_down += 1
            else:
                upright += 1
        return upright, upside_down


class VlPipeline:
    """Runs the scan path for one PDF at a time; models stay loaded across PDFs."""

    def __init__(self, model_path: Path, device: str, vram_gb: float | None = None) -> None:
        self.recognizer = VlRecognizer(model_path, device, vram_gb)
        self.paddle = PaddleModels()
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paddle")

    def run(self, pdf_path: Path) -> list[PageText]:
        with tempfile.TemporaryDirectory(prefix="ocr_vl_") as tmp:
            rendered = render_pages(pdf_path, Path(tmp))
            futures = [self._worker.submit(self._prepare, page) for page in rendered]
            try:
                return self._read_all(futures)
            finally:
                for future in futures:
                    future.cancel()
                wait(futures)

    def close(self) -> None:
        self._worker.shutdown(wait=True, cancel_futures=True)

    def _read_all(self, futures: list[Future[PreparedPage]]) -> list[PageText]:
        results: list[PageText] = []
        for start in range(0, len(futures), PAGES_PER_ROUND):
            group = [future.result() for future in futures[start : start + PAGES_PER_ROUND]]
            owners = [block for page in group for block, _ in page.crops]
            items = [(crop, block.task) for page in group for block, crop in page.crops]
            for block, text in zip(owners, self.recognizer.read(items), strict=True):
                block.text = text
            self.recognizer.release_cache()
            for page in group:
                markdown = page_markdown(page.blocks)
                warnings = list(page.warnings)
                if not markdown.strip():
                    warnings.append("empty page text")
                results.append(PageText(page.index, markdown, warnings))
        joined = join_page_tables([page.markdown for page in results])
        for page, markdown in zip(results, joined, strict=True):
            page.markdown = markdown
        return results

    def _prepare(self, page: RenderedPage) -> PreparedPage:
        """CPU part of a page; a failure becomes a page warning instead of failing the PDF."""
        prepared = PreparedPage(page.index)
        try:
            rotated = self._upright(page.png, prepared.warnings)
            prepared.blocks = self.paddle.layout(page.png)
            with Image.open(page.png) as source:
                image = source.convert("RGB")
            hi_image = None
            if page.hi_png is not None:
                with Image.open(page.hi_png) as source:
                    hi_image = source.convert("RGB")
                if rotated:
                    hi_image = hi_image.rotate(rotated, expand=True)
            prepared.crops = crop_blocks(image, prepared.blocks, hi_image)
        except Exception as error:  # noqa: BLE001 - one unreadable page must not lose the rest of the PDF
            prepared.blocks, prepared.crops = [], []
            prepared.warnings.append(f"layout failed: {type(error).__name__}: {error}")
        return prepared

    def _upright(self, png: Path, warnings: list[str]) -> int:
        """Rotate the page image in place when it was scanned sideways; returns the angle used."""
        angle, score = self.paddle.page_orientation(png)
        if not angle or score < ORIENTATION_MIN_SCORE:
            return 0
        with Image.open(png) as source:
            image = source.copy()
        turned = image.rotate(angle, expand=True)
        try:
            upright, upside_down = self.paddle.line_votes(turned)
            if upside_down > upright and upside_down >= 3:
                angle = (angle + 180) % 360
                turned = image.rotate(angle, expand=True) if angle else image
        except Exception as error:  # noqa: BLE001 - the vote only double-checks the classifier
            warnings.append(f"orientation vote failed: {type(error).__name__}: {error}")
        if angle:
            turned.save(png)
        return angle
