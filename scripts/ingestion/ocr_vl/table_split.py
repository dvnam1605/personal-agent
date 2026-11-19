"""Split a table that layout merged with the section headings between borderless tables.

Financial-report notes are the typical case: '3c. Tài sản ... như sau:' and '4. Tài sản tài
chính ...' come back as rows of one big table. The rule works on the final Markdown:

- a row whose only text is in its first cell and is a numbered section heading
  ('3c. ...', '4. ...', 'II. ...', 'a) ...') or ends with ':' becomes '## heading' between two
  tables;
- a long numbered heading glued into the first cell of a column-header row is lifted out and the
  header row stays with an empty first cell;
- once a table is known to be several tables, a label-only row right after a total row
  ('Cộng', 'Tổng ...') is a sub-table caption and splits too.

Rows inside a rowspan are never split, and a piece left without any number cell becomes plain
text. Ported unchanged in behaviour from the OCR web app (``table_split.py``).
"""

from __future__ import annotations

import re

TABLE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.S | re.I)
ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<t([dh])\b([^>]*)>(.*?)</t[dh]>", re.S | re.I)
HEADING = re.compile(
    r"^\s*((\d{1,2}(\.\d{1,2})*[a-zđ]?|[IVX]{1,5}|[a-zđ])[.)]\s+\S.{8,}|.{12,}:\s*)$", re.S
)
TOTAL = re.compile(r"^\s*(cộng|tổng|total)\b", re.I)
NUM = re.compile(r"\d[\d.,]*\d|\d")
AMOUNT = re.compile(r"\d{1,3}([.,]\d{3})+|^\(?-?\d+([.,]\d+)?\)?%?$")

Cell = tuple[str, str]
Row = tuple[str, list[Cell]]
Piece = tuple[str, str]


def _text(cell_html: str) -> str:
    return re.sub(r"<[^>]+>", "", cell_html).strip()


def _span(attrs: str, name: str) -> int:
    match = re.search(name + r"\s*=\s*['\"]?(\d+)", attrs, re.I)
    return int(match.group(1)) if match else 1


def _parse(body: str) -> list[Row]:
    rows: list[Row] = []
    for row in ROW.finditer(body):
        cells = [(m.group(2), _text(m.group(3))) for m in CELL.finditer(row.group(1))]
        rows.append((row.group(0), cells))
    return rows


def _label_only(cells: list[Cell]) -> bool:
    return (
        bool(cells)
        and bool(cells[0][1])
        and all(not text for _, text in cells[1:])
        and _span(cells[0][0], "rowspan") == 1
    )


def _heading_cuts(rows: list[Row]) -> dict[int, str]:
    cuts: dict[int, str] = {}
    reach = 0
    for i, (_, cells) in enumerate(rows):
        if (
            i > reach
            and i > 0
            and _label_only(cells)
            and HEADING.match(cells[0][1])
            and not NUM.fullmatch(cells[0][1])
        ):
            cuts[i] = "heading"
        for attrs, _ in cells:
            reach = max(reach, i + _span(attrs, "rowspan") - 1)
    return cuts


def _lifted_headings(rows: list[Row], cuts: dict[int, str]) -> dict[int, str]:
    lifted: dict[int, str] = {}
    for i, (_, cells) in enumerate(rows):
        if i in cuts or len(cells) < 2 or _span(cells[0][0], "rowspan") > 1:
            continue
        first, rest = cells[0][1], [text for _, text in cells[1:]]
        if (
            len(first) >= 25
            and HEADING.match(first)
            and any(rest)
            and not any(AMOUNT.search(text) for text in rest)
        ):
            lifted[i] = first
    return lifted


def _lift(rows: list[Row], cuts: dict[int, str], lifted: dict[int, str]) -> None:
    for i, first in lifted.items():
        raw, cells = rows[i]
        start = raw.find(">", raw.lower().find("<t", 3)) + 1
        end = raw.lower().find("</t", start)
        rows[i] = (raw[:start] + raw[end:], [(cells[0][0], ""), *cells[1:]])
        cuts[i] = "lift:" + first


def _caption_cuts(rows: list[Row], cuts: dict[int, str]) -> None:
    reach = 0
    for i, (_, cells) in enumerate(rows):
        prev_total = (
            bool(rows[i - 1][1]) and bool(TOTAL.match(rows[i - 1][1][0][1])) if i else False
        )
        if (
            i not in cuts
            and i > reach
            and i > 0
            and _label_only(cells)
            and (prev_total or cuts.get(i - 1) == "heading")
            and len(cells[0][1]) < 60
            and i + 1 < len(rows)
            and any(NUM.search(text) for _, text in rows[i + 1][1][1:])
        ):
            cuts[i] = "caption"
        for attrs, _ in cells:
            reach = max(reach, i + _span(attrs, "rowspan") - 1)


def _pieces(open_tag: str, rows: list[Row], cuts: dict[int, str]) -> list[Piece]:
    pieces: list[Piece] = []
    current: list[Row] = []

    def flush() -> None:
        if not current:
            return
        has_number = any(NUM.search(text) for row in current for _, text in row[1])
        if has_number and (len(current) > 1 or len(current[0][1]) > 1):
            pieces.append(("table", open_tag + "".join(row[0] for row in current) + "</table>"))
        else:
            pieces.extend(("text", " ".join(t for _, t in row[1] if t)) for row in current)
        current.clear()

    for i, row in enumerate(rows):
        cut = cuts.get(i, "")
        if cut.startswith("lift:"):
            flush()
            pieces.append(("heading", cut[5:]))
            current.append(row)
        elif cut:
            flush()
            pieces.append(("heading" if cut == "heading" else "text", row[1][0][1]))
        else:
            current.append(row)
    flush()
    return pieces


def split_table(html_table: str) -> list[Piece] | None:
    """One HTML table -> ('table' | 'heading' | 'text', content) pieces, or None to keep it."""
    match = TABLE.match(html_table)
    if not match:
        return None
    open_tag = html_table[: html_table.index(">") + 1]
    rows = _parse(match.group(1))
    if len(rows) < 4:
        return None
    cuts = _heading_cuts(rows)
    lifted = _lifted_headings(rows, cuts)
    if not cuts and not lifted:
        return None
    _lift(rows, cuts, lifted)
    _caption_cuts(rows, cuts)
    return _pieces(open_tag, rows, cuts)


def split_tables(markdown: str) -> str:
    """Apply :func:`split_table` to every HTML table in a Markdown page."""

    def replace(match: re.Match[str]) -> str:
        pieces = split_table(match.group(0))
        if not pieces:
            return match.group(0)
        return "\n\n".join("## " + text if kind == "heading" else text for kind, text in pieces)

    return TABLE.sub(replace, markdown)
