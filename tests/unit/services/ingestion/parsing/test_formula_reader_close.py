"""The Docling formula reader releases its PyMuPDF document deterministically."""

from __future__ import annotations

import pymupdf

from app.services.ingestion.parsing.docling_parser import _FormulaTextReader


def _pdf_bytes() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "E = mc^2")
    data = doc.tobytes()
    doc.close()
    return data


def test_context_exit_closes_the_opened_document() -> None:
    with _FormulaTextReader(_pdf_bytes(), "a.pdf") as reader:
        opened = reader._open()
        assert opened is not None
    assert opened.is_closed
    assert reader._open() is None


def test_close_without_opening_is_a_no_op() -> None:
    reader = _FormulaTextReader(b"not a pdf", "a.docx")
    reader.close()
    assert reader._open() is None
