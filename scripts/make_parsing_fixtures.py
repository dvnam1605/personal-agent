"""Generate the P9B parsing fixture corpus under tests/fixtures/parsing/.

Deterministic, dependency-free generator producing:
  pdf/       normal_text.pdf multicolumn.pdf table_heavy.pdf long_sections.pdf scanned.pdf
  docx/      headings_paragraphs.docx lists.docx tables.docx long_hierarchical.docx
  markdown/  preparsed_sample.md
  failure/   corrupt.pdf unsupported.bin

Run from repo root: python scripts/make_parsing_fixtures.py
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "parsing"


# --------------------------------------------------------------------------- #
# Minimal PDF writer                                                          #
# --------------------------------------------------------------------------- #


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _text_op(x: float, y: float, size: float, text: str) -> str:
    return f"BT 1 0 0 1 {x} {y} Tm /F1 {size} Tf ({_escape(text)}) Tj ET\n"


def make_pdf(
    pages: list[list[str]],
    *,
    image_page_index: int | None = None,
    truncate_bytes: int | None = None,
) -> bytes:
    """Assemble a small valid PDF; one content-op list per page."""
    page_count = len(pages)
    kids = " ".join(f"{4 + i * 2} 0 R" for i in range(page_count))
    image_obj_no = 4 + page_count * 2 if image_page_index is not None else None
    body_objects: list[bytes] = []

    body_objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    body_objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())
    body_objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for index, ops in enumerate(pages):
        image_resources = ""
        image_draw = ""
        if index == image_page_index and image_obj_no is not None:
            image_resources = f"/XObject << /Im1 {image_obj_no} 0 R >>"
            image_draw = "q 612 0 0 792 0 0 cm /Im1 Do Q\n"
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> {image_resources} >> "
            f"/Contents {5 + index * 2} 0 R >>"
        ).encode()
        stream = "".join(ops).encode() + image_draw.encode()
        content_obj = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        body_objects.append(page_dict)
        body_objects.append(content_obj)

    if image_obj_no is not None:
        pixels = bytes([200, 200, 120, 120, 90, 90, 60, 60, 40])
        image_stream = (
            (
                "<< /Type /XObject /Subtype /Image /Width 3 /Height 3 "
                "/ColorSpace /DeviceGray /BitsPerComponent 8 /Length "
                + str(len(pixels))
                + " >>\nstream\n"
            ).encode()
            + pixels
            + b"\nendstream"
        )
        body_objects.append(image_stream)

    out = BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(body_objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode())
        out.write(obj)
        out.write(b"\nendobj\n")

    xref_offset = out.tell()
    total = len(body_objects) + 1
    out.write(f"xref\n0 {total}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )

    data = out.getvalue()
    if truncate_bytes is not None:
        data = data[:truncate_bytes]
    return data


# --------------------------------------------------------------------------- #
# Minimal OOXML (.docx) writer                                                #
# --------------------------------------------------------------------------- #

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/></w:style>
</w:styles>"""

_DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _paragraph(text: str, style: str | None = None) -> str:
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{props}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def make_docx(paragraphs: list[str]) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W_NS}"><w:body>' + "".join(paragraphs) + "</w:body></w:document>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS)
        archive.writestr("word/styles.xml", _STYLES)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Fixture definitions                                                         #
# --------------------------------------------------------------------------- #


def build_pdf_fixtures() -> tuple[dict[str, bytes], dict[str, bytes]]:
    normal_text = make_pdf(
        [
            [
                _text_op(72, 720, 18, "Quarterly Operations Review"),
                _text_op(
                    72, 690, 11, "This report summarises operational metrics for the quarter."
                ),
                _text_op(
                    72, 674, 11, "Revenue grew steadily while support backlog remained stable."
                ),
                _text_op(
                    72, 658, 11, "Infrastructure costs decreased after the migration project."
                ),
                _text_op(
                    72, 642, 11, "Headcount finished flat after planned attrition adjustments."
                ),
            ]
        ]
    )

    multicolumn = make_pdf(
        [
            [
                _text_op(216, 730, 16, "Two Column Newsletter"),
                _text_op(
                    54, 700, 10, "Left column carries the main story about platform reliability"
                ),
                _text_op(
                    54, 686, 10, "work completed during the incident response overhaul this year."
                ),
                _text_op(
                    330, 700, 10, "Right column lists community events scheduled for the coming"
                ),
                _text_op(
                    330, 686, 10, "months including meetups, workshops, and training sessions."
                ),
            ]
        ]
    )

    table_rows: list[str] = []
    header_x = [72, 192, 312, 432]
    columns = ["Region", "Revenue", "Growth", "Notes"]
    rows = [
        ["North", "1200", "4%", "steady demand"],
        ["South", "980", "7%", "new stores"],
        ["East", "1430", "2%", "flat market"],
        ["West", "1105", "9%", "best quarter"],
    ]
    for x, cell in zip(header_x, columns, strict=True):
        table_rows.append(_text_op(x, 720, 11, cell))
    for row_index, row in enumerate(rows):
        y = 700 - row_index * 16
        for x, cell in zip(header_x, row, strict=True):
            table_rows.append(_text_op(x, y, 10, cell))
    table_heavy = make_pdf([table_rows])

    long_pages: list[list[str]] = []
    for chapter in range(1, 4):
        page_ops = [_text_op(72, 730, 16, f"Chapter {chapter}: Systems")]
        for section in range(1, 6):
            page_ops.append(
                _text_op(72, 706 - section * 24, 13, f"Section {chapter}.{section} Overview")
            )
            page_ops.append(
                _text_op(
                    72,
                    694 - section * 24,
                    10,
                    f"Detailed discussion text for chapter {chapter} section {section} follows here.",
                )
            )
        long_pages.append(page_ops)
    long_sections = make_pdf(long_pages)

    scanned = make_pdf([[]], image_page_index=0)

    corrupt = make_pdf([[_text_op(72, 720, 12, "This file will be truncated")]], truncate_bytes=340)

    return {
        "normal_text.pdf": normal_text,
        "multicolumn.pdf": multicolumn,
        "table_heavy.pdf": table_heavy,
        "long_sections.pdf": long_sections,
        "scanned.pdf": scanned,
    }, {"corrupt.pdf": corrupt}


def build_docx_fixtures() -> dict[str, bytes]:
    headings = make_docx(
        [
            _paragraph("Project Handbook", "Heading1"),
            _paragraph("Onboarding", "Heading2"),
            _paragraph("Every new engineer pairs with a mentor during the first month."),
            _paragraph("Environment Setup", "Heading2"),
            _paragraph("Install the toolchain, clone the repository, and run the test suite."),
            _paragraph("Daily Workflow", "Heading1"),
            _paragraph("Pick one story from the board and move it through review."),
        ]
    )
    lists = make_docx(
        [
            _paragraph("Checklist", "Heading1"),
            _paragraph("Prepare the demo environment", "ListParagraph"),
            _paragraph("Verify credentials are loaded", "ListParagraph"),
            _paragraph("Record the session for absentees", "ListParagraph"),
            _paragraph("Numbered priorities follow the same pattern."),
        ]
    )
    tables = make_docx(
        [
            _paragraph("Budget Table", "Heading1"),
            "<w:tbl><w:tr>"
            "<w:tc><w:p><w:r><w:t>Item</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Cost</w:t></w:r></w:p></w:tc>"
            "</w:tr><w:tr>"
            "<w:tc><w:p><w:r><w:t>Licenses</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>500</w:t></w:r></w:p></w:tc>"
            "</w:tr></w:tbl>",
            _paragraph("Totals are reviewed monthly by finance."),
        ]
    )
    long_doc: list[str] = []
    for part in range(1, 4):
        long_doc.append(_paragraph(f"Part {part}", "Heading1"))
        for chapter in range(1, 4):
            long_doc.append(_paragraph(f"Chapter {part}.{chapter}", "Heading2"))
            for sub in range(1, 3):
                long_doc.append(_paragraph(f"Section {part}.{chapter}.{sub}", "Heading3"))
                long_doc.append(
                    _paragraph(
                        f"Body content for part {part}, chapter {chapter}, section {sub}. "
                        "It spans a couple of sentences so chunkers have material."
                    )
                )
    return {
        "headings_paragraphs.docx": headings,
        "lists.docx": lists,
        "tables.docx": tables,
        "long_hierarchical.docx": make_docx(long_doc),
    }


PREPARSED_SAMPLE = """# Deployment Runbook

## Preconditions

Confirm the release tag exists and CI is green before starting.

| Step | Command | Owner |
| --- | --- | --- |
| Build | make release | platform |
| Verify | make smoke-test | qa |

## Rollout

- Announce the window in the status channel
- Apply migrations with the locked deploy account
- Watch error dashboards for ten minutes

### Rollback

Revert to the previous artifact and re-run smoke tests.
"""


def main() -> None:
    pdf_fixtures, failure_pdf = build_pdf_fixtures()
    targets: dict[Path, bytes] = {}

    for name, data in pdf_fixtures.items():
        targets[FIXTURE_ROOT / "pdf" / name] = data
    for name, data in failure_pdf.items():
        targets[FIXTURE_ROOT / "failure" / name] = data
    for name, data in build_docx_fixtures().items():
        targets[FIXTURE_ROOT / "docx" / name] = data
    targets[FIXTURE_ROOT / "markdown" / "preparsed_sample.md"] = PREPARSED_SAMPLE.encode()
    targets[FIXTURE_ROOT / "failure" / "unsupported.bin"] = b"\x00\x01\x02\xffnot-a-document"

    for path, data in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"wrote {path.relative_to(FIXTURE_ROOT.parents[2])} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
