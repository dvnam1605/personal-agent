"""Rebuild numbered equations as LaTeX after parsing.

The PDF text layer keeps an equation's symbols but loses its layout
("Pn i=1 aibi pPn i=1 a2 i" for a fraction of sums). One LLM call per
equation rebuilds the structure from those symbols and the sentence that
introduces it; the result is kept only when it parses as LaTeX. The node
then reads "Công thức (3.2): $$…$$", otherwise it stays as parsed.
"""

from __future__ import annotations

import asyncio
import logging
import re

import latex2mathml.converter

from app.core.llm import llm_json
from app.domain.models.ingestion.parsed_document import ParagraphNode, ParsedDocument

logger = logging.getLogger(__name__)

CONCURRENCY = 3
TIMEOUT_SECONDS = 60.0
LEAD_CHARS = 400

# "Công thức: cos_sim(a, b) = … (3.2)" as written by the PDF parsers.
_PARSED = re.compile(r"^Công thức:\s*(?P<body>.+?)\s*\((?P<num>\d+(?:\.\d+)+)\)\s*$", re.DOTALL)
# The same equation as one line inside stored chunk text.
_PARSED_LINE = re.compile(
    r"^Công thức:[ \t]*(?P<body>[^\n]+?)[ \t]*\((?P<num>\d+(?:\.\d+)+)\)[ \t]*$", re.MULTILINE
)
# A cases/array row opening with "[" is read by latex2mathml as an alignment option.
_ROW_BRACKET = re.compile(r"(\\begin\{(?:cases|array|aligned)\}|\\\\)\s*\[")

_SYSTEM = (
    "Bạn chuyển công thức toán bị mất cấu trúc (trích từ lớp chữ của PDF) thành LaTeX chuẩn. "
    "Chỉ dựng lại cấu trúc (phân số, tổng, căn, chỉ số, hệ cases) từ đúng các ký hiệu có trong "
    "chuỗi gốc và câu dẫn; không thêm, bớt hay đổi ký hiệu, giữ nguyên chữ tiếng Việt bằng "
    '\\text{...}. Không kèm số hiệu công thức. Trả về JSON {"latex": "..."}.'
)


def normalize_latex(latex: str) -> str:
    latex = latex.strip().strip("$").strip()
    return _ROW_BRACKET.sub(r"\1 {}[", latex)


def is_valid_latex(latex: str) -> bool:
    if not latex:
        return False
    try:
        latex2mathml.converter.convert(latex)
    except Exception:  # noqa: BLE001 - any parser error means unusable
        return False
    return True


async def rebuild_latex(body: str, number: str, lead: str) -> str | None:
    """LaTeX for one text-layer equation, or None when the reply does not parse."""
    reply = await llm_json(
        _SYSTEM,
        f"Câu dẫn: {lead[-LEAD_CHARS:]}\nChuỗi gốc ({number}): {body}",
        timeout=TIMEOUT_SECONDS,
        temperature=0,
    )
    latex = normalize_latex(str((reply or {}).get("latex") or ""))
    return latex if is_valid_latex(latex) else None


def latex_line(number: str, latex: str) -> str:
    return f"Công thức ({number}): $${latex}$$"


async def rebuild_in_text(text: str, cache: dict[tuple[str, str], str | None]) -> str:
    """Stored chunk text with its text-layer equation lines rebuilt as LaTeX.

    ``cache`` is keyed by (number, body), so the same equation in a parent
    chunk and its child chunk costs one call.
    """
    out: list[str] = []
    last = 0
    for m in _PARSED_LINE.finditer(text):
        key = (m.group("num"), m.group("body").strip())
        if key not in cache:
            cache[key] = await rebuild_latex(key[1], key[0], text[: m.start()])
        if latex := cache[key]:
            out.extend([text[last : m.start()], latex_line(key[0], latex)])
            last = m.end()
    out.append(text[last:])
    return "".join(out)


class FormulaLatexRebuilder:
    """Replaces parsed equation paragraphs with their LaTeX form."""

    async def arebuild(self, document: ParsedDocument) -> ParsedDocument:
        targets = [
            (i, m)
            for i, node in enumerate(document.nodes)
            if isinstance(node, ParagraphNode) and (m := _PARSED.match(node.text))
        ]
        if not targets:
            return document
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def rebuild(index: int, match: re.Match[str]) -> tuple[int, str | None]:
            lead = " ".join(n.text for n in document.nodes[max(0, index - 2) : index])
            async with semaphore:
                latex = await rebuild_latex(match.group("body"), match.group("num"), lead)
            return index, latex

        results = dict(await asyncio.gather(*(rebuild(i, m) for i, m in targets)))
        nodes = list(document.nodes)
        for index, match in targets:
            latex = results.get(index)
            if latex:
                nodes[index] = nodes[index].model_copy(
                    update={"text": latex_line(match.group("num"), latex)}
                )
        logger.info(
            "formula_latex_rebuilt total=%d rebuilt=%d",
            len(targets),
            sum(1 for v in results.values() if v),
        )
        return document.model_copy(update={"nodes": tuple(nodes)})
