"""Equations parsed from the PDF text layer are rebuilt as LaTeX."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.domain.models.ingestion.parsed_document import (
    ParagraphNode,
    ParsedDocument,
    ParsedDocumentMetadata,
)
from app.services.ingestion.enrichment import formulas as module
from app.services.ingestion.enrichment.formulas import (
    FormulaLatexRebuilder,
    is_valid_latex,
    normalize_latex,
)


def _doc(*texts: str) -> ParsedDocument:
    return ParsedDocument(
        metadata=ParsedDocumentMetadata(
            source_id="s", filename="a.pdf", parser_name="docling", parser_version="1"
        ),
        nodes=tuple(ParagraphNode(node_id=f"n{i}", order=i, text=t) for i, t in enumerate(texts)),
    )


def test_row_opening_with_a_bracket_is_kept_parseable() -> None:
    latex = r"\begin{cases} [a, b], & x > 0, \\ [c, d], & x = 0. \end{cases}"
    assert not is_valid_latex(latex)
    assert is_valid_latex(normalize_latex(f"$${latex}$$"))


def test_equations_are_rebuilt_when_the_reply_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = iter([{"latex": r"S(d) = \sum_{i} \frac{1}{k + r_i(d)}"}, {"latex": r"\frac{"}])
    prompts: list[str] = []

    async def fake_llm(system: str, user: str, **_: Any) -> dict[str, Any]:
        prompts.append(user)
        return next(replies)

    monkeypatch.setattr(module, "llm_json", fake_llm)
    monkeypatch.setattr(module, "CONCURRENCY", 1)
    doc = _doc(
        "Điểm RRF cho sản phẩm d được tính như sau:",
        "Công thức: SRRF(d) = X i:d∈Li 1 k + ranki(d) (3.3)",
        "Công thức: broken ( x (3.4)",
    )
    out = asyncio.run(FormulaLatexRebuilder().arebuild(doc))
    assert out.nodes[1].text == r"Công thức (3.3): $$S(d) = \sum_{i} \frac{1}{k + r_i(d)}$$"
    assert out.nodes[2].text == "Công thức: broken ( x (3.4)"  # invalid reply: kept as parsed
    assert "Điểm RRF" in prompts[0] and "(3.3)" in prompts[0]


def test_documents_without_equations_make_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*_: Any, **__: Any) -> None:
        raise AssertionError("no LLM call expected")

    monkeypatch.setattr(module, "llm_json", fail)
    doc = _doc("Chỉ có văn bản.")
    assert asyncio.run(FormulaLatexRebuilder().arebuild(doc)) is doc
