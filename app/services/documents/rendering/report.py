"""Render a written document to Markdown (UI) and .docx (download).

The .docx uses python-docx (installed with docling). Administrative kinds
get the national motto header block; every document ends with a numbered
"Tài liệu tham khảo" list matching the inline [n] markers.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from app.services.documents.templates import DocTemplate

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)_(.+?)_(?!\*)")


def source_line(c: dict[str, Any]) -> str:
    bits = [str(c.get("title") or c.get("document_id"))]
    if c.get("section_title"):
        bits.append(f"mục {c['section_title']}")
    if c.get("page_start"):
        bits.append(f"tr. {c['page_start']}")
    return ", ".join(bits)


def render_markdown(
    title: str,
    sections: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    *,
    template: DocTemplate,
    created: date | None = None,
) -> str:
    created = created or date.today()
    parts = [f"# {title}", f"_{template.label} — ngày {created:%d/%m/%Y}_", ""]
    for s in sections:
        if s.get("status") == "missing":
            continue
        parts.extend([f"## {s['heading']}", "", s["text"].strip(), ""])
    if citations:
        parts.extend(["## Tài liệu tham khảo", ""])
        parts.extend(f"[{c['number']}] {source_line(c)}" for c in citations)
    return "\n".join(parts).strip() + "\n"


def _add_runs(paragraph: Any, text: str) -> None:
    """Minimal inline Markdown: **bold** and _italic_."""
    pos = 0
    tokens: list[tuple[int, int, str, str]] = []
    for m in _BOLD.finditer(text):
        tokens.append((m.start(), m.end(), "b", m.group(1)))
    for m in _ITALIC.finditer(text):
        if not any(s <= m.start() < e for s, e, _, _ in tokens):
            tokens.append((m.start(), m.end(), "i", m.group(1)))
    for start, end, kind, inner in sorted(tokens):
        if start < pos:
            continue
        if start > pos:
            paragraph.add_run(text[pos:start])
        run = paragraph.add_run(inner)
        run.bold = kind == "b"
        run.italic = kind == "i"
        pos = end
    if pos < len(text):
        paragraph.add_run(text[pos:])


def _add_block(doc: Any, block: str) -> None:
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        if stripped.startswith("### "):
            doc.add_heading(stripped[4:], level=3)
        elif stripped.startswith(("- ", "* ", "• ")):
            _add_runs(doc.add_paragraph(style="List Bullet"), stripped[2:])
        elif re.match(r"^\d+[.)]\s", stripped):
            _add_runs(doc.add_paragraph(style="List Number"), re.sub(r"^\d+[.)]\s", "", stripped))
        elif stripped.startswith("|"):
            doc.add_paragraph(stripped)  # tables stay readable as text
        else:
            _add_runs(doc.add_paragraph(), stripped)


def render_docx(
    path: Path,
    title: str,
    sections: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    *,
    template: DocTemplate,
    issuer: str | None = None,
    created: date | None = None,
) -> Path:
    created = created or date.today()
    doc = DocxDocument()
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(13)

    if template.formal_header:
        table = doc.add_table(rows=1, cols=2)
        left, right = table.rows[0].cells
        left.text = (issuer or "CƠ QUAN, ĐƠN VỊ").upper()
        right.text = "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\nĐộc lập - Tự do - Hạnh phúc"
        for cell in (left, right):
            for p in cell.paragraphs:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in p.runs:
                    run.bold = True
        place = doc.add_paragraph(f"…, ngày {created.day} tháng {created.month} năm {created.year}")
        place.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    heading = doc.add_heading(template.label.upper() if template.formal_header else title, 0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if template.formal_header:
        sub = doc.add_paragraph(title)
        sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in sub.runs:
            run.bold = True
    else:
        meta = doc.add_paragraph(f"{template.label} — ngày {created:%d/%m/%Y}")
        meta.alignment = WD_ALIGN_PARAGRAPH.CENTER

    for s in sections:
        if s.get("status") == "missing":
            continue
        doc.add_heading(s["heading"], level=1)
        _add_block(doc, s["text"])

    if citations:
        doc.add_heading("Tài liệu tham khảo", level=1)
        for c in citations:
            doc.add_paragraph(f"[{c['number']}] {source_line(c)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path
