"""Layout blocks and the geometric clean-up applied to PP-DocLayoutV2 output.

A page is a list of :class:`Block` in reading order. Each layout label decides how the block is
read (``task``: the VL prompt) and how it is written to Markdown. Three fixes measured on the OCR
web app run after detection, all on box geometry only:

- a text box that wraps two lines which also have their own boxes is dropped (read twice);
- a multi-line display formula cut into one box per line is merged back into one block;
- a table box that runs into the line just above/below it is pulled back to that line.

Nothing here imports Paddle; :mod:`.pipeline` feeds it the detector's JSON.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

TASK_OF = {"table": "table", "display_formula": "formula", "chart": "chart"}
IMAGE_LABELS = frozenset({"image", "header_image", "footer_image", "seal"})
SKIP_LABELS = frozenset({"inline_formula"})
TRIM_LABELS = frozenset(
    {
        "text",
        "paragraph_title",
        "figure_title",
        "doc_title",
        "abstract",
        "content",
        "reference",
        "algorithm",
    }
)
MARGIN_LABELS = frozenset(
    {"header", "footer", "number", "footnote", "aside_text", "header_image", "footer_image"}
)


@dataclass
class Block:
    """One layout region: label, box ``[x1, y1, x2, y2]`` in page pixels, and its read text."""

    label: str
    box: list[float]
    score: float = 1.0
    order: int | None = None
    text: str | None = None

    @property
    def task(self) -> str:
        return TASK_OF.get(self.label, "ocr")

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]


def blocks_from_layout(payload: Mapping[str, Any]) -> list[Block]:
    """PP-DocLayoutV2 result JSON -> cleaned blocks in reading order."""
    data = payload.get("res", payload)
    blocks: list[Block] = []
    for raw in data.get("boxes", []):
        x1, y1, x2, y2 = (float(v) for v in raw["coordinate"])
        order = raw.get("order")
        blocks.append(
            Block(
                label=str(raw["label"]),
                box=[x1, y1, x2, y2],
                score=round(float(raw["score"]), 3),
                order=int(order) if order is not None else None,
            )
        )
    blocks = [b for b in blocks if b.label not in SKIP_LABELS]
    if blocks and all(b.order is not None for b in blocks):
        blocks.sort(key=lambda b: b.order or 0)
    return trim_tables(merge_formula_lines(drop_wrapping_text(blocks)))


def _inside_share(inner: list[float], outer: list[float]) -> float:
    w = min(inner[2], outer[2]) - max(inner[0], outer[0])
    h = min(inner[3], outer[3]) - max(inner[1], outer[1])
    area = max(1.0, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return max(0.0, w) * max(0.0, h) / area


def drop_wrapping_text(blocks: list[Block]) -> list[Block]:
    """Drop a text box when two or more other text boxes inside it cover most of its height."""
    drop: set[int] = set()
    for i, outer in enumerate(blocks):
        if outer.label not in TRIM_LABELS:
            continue
        inner = [
            other
            for j, other in enumerate(blocks)
            if j != i and other.label in TRIM_LABELS and _inside_share(other.box, outer.box) > 0.85
        ]
        if len(inner) >= 2 and sum(b.height for b in inner) > 0.7 * outer.height:
            drop.add(i)
    return [b for i, b in enumerate(blocks) if i not in drop]


def merge_formula_lines(blocks: list[Block]) -> list[Block]:
    """Join consecutive display-formula lines that touch and share the left edge into one block."""
    out: list[Block] = []
    for block in blocks:
        prev = out[-1] if out else None
        if prev is not None and prev.label == block.label == "display_formula":
            line = min(prev.height, block.height)
            gap = block.box[1] - prev.box[3]
            overlap = min(prev.box[2], block.box[2]) - max(prev.box[0], block.box[0])
            aligned = abs(prev.box[0] - block.box[0]) < 40 or overlap > 0.6 * min(
                prev.width, block.width
            )
            if -0.3 * line < gap < 0.35 * line and aligned:
                prev.box = [
                    min(prev.box[0], block.box[0]),
                    prev.box[1],
                    max(prev.box[2], block.box[2]),
                    block.box[3],
                ]
                continue
        out.append(block)
    return out


def trim_tables(blocks: list[Block]) -> list[Block]:
    """Pull a table edge back to a body-text line it cuts through (a caption read as a row)."""
    for table in blocks:
        if table.label != "table":
            continue
        table_height = table.height
        for block in blocks:
            if block is table or block.label not in TRIM_LABELS:
                continue
            bh = block.height
            overlap = min(table.box[2], block.box[2]) - max(table.box[0], block.box[0])
            if overlap <= 0 or bh > 0.15 * table_height:
                continue
            if table.box[3] - 0.9 * bh - 4 < block.box[1] < table.box[3] <= block.box[3] + 2:
                table.box[3] = block.box[1] - 2
            elif block.box[1] - 2 <= table.box[1] < block.box[3] < table.box[1] + 0.9 * bh + 4:
                table.box[1] = block.box[3] + 2
    return blocks
