"""Read blocks -> Markdown, following the OCR web app's export rules.

- Tables come back from the model as OTSL (``<fcel>``/``<lcel>``/``<ucel>``/``<xcel>``/``<nl>``)
  and are written as HTML tables with row/col spans; LaTeX symbols inside cells become Unicode.
- Display formulas become ``$$ ... $$`` after a light LaTeX repair.
- Short blocks that sit side by side (two-column headers of official documents, 'Nơi nhận |
  chữ ký') become a borderless HTML table so the row/column relation survives.
- Page margins (header, footer, page number, footnote) are left out; image regions are dropped
  because the batch does not keep image files.
- A table that runs over a page break is joined back into the previous page's table.
"""

from __future__ import annotations

import html
import re

from .layout import IMAGE_LABELS, MARGIN_LABELS, Block
from .table_split import split_tables

CELL_TEX = {
    "leq": "≤",
    "le": "≤",
    "geq": "≥",
    "ge": "≥",
    "neq": "≠",
    "ne": "≠",
    "pm": "±",
    "times": "×",
    "div": "÷",
    "approx": "≈",
    "cdot": "·",
    "rightarrow": "→",
    "to": "→",
    "leftarrow": "←",
    "Rightarrow": "⇒",
    "infty": "∞",
    "alpha": "α",
    "beta": "β",
    "Delta": "Δ",
    "mu": "μ",
    "Omega": "Ω",
    "%": "%",
    "circ": "°",
    "degree": "°",
}
LATEX_TEXT = re.compile(r"^\\(operatorname|frac|sum|int|lim|left|sqrt|mathbf|begin)|\\frac\{|\^\{")
OTSL_CELL = re.compile(r"<(fcel|ecel|lcel|ucel|xcel)>([^<]*)")
DATA_ROW_START = re.compile(r"^\s*(\d+[.)]?|[IVXLC]+[.)]?|[a-zđ][.)]|[-–+])(\s|$)")


def cell_text(text: str) -> str:
    """Table cells are plain text: LaTeX the model writes in a cell goes back to Unicode."""
    text = text.strip()
    if "\\" not in text and "$" not in text:
        return text
    text = re.sub(r"\$([^$]{1,40})\$", r"\1", text)
    text = re.sub(r"\^\{?\\circ\}?", "°", text)
    return re.sub(
        r"\\(%|[A-Za-z]+)(?![A-Za-z])", lambda m: CELL_TEX.get(m.group(1), m.group(0)), text
    )


def otsl_to_html(otsl: str) -> str:
    """OTSL table tokens -> ``<table>`` with rowspan/colspan; short rows are padded."""
    rows = [row for row in otsl.split("<nl>") if row.strip()]
    grid = [OTSL_CELL.findall(row) for row in rows]
    width = max((len(row) for row in grid), default=0)
    for i, row in enumerate(grid):
        while len(row) < width:
            up = grid[i - 1][len(row)] if i else None
            row.append(("ucel" if up and up[0] == "ucel" else "ecel", ""))
    out: list[str] = []
    for i, row in enumerate(grid):
        cells: list[str] = []
        for j, (token, text) in enumerate(row):
            if token not in ("fcel", "ecel"):
                continue
            colspan = 1
            while j + colspan < len(row) and row[j + colspan][0] in ("lcel", "xcel"):
                colspan += 1
            rowspan = 1
            while (
                i + rowspan < len(grid)
                and j < len(grid[i + rowspan])
                and grid[i + rowspan][j][0] in ("ucel", "xcel")
            ):
                rowspan += 1
            attrs = (f' rowspan="{rowspan}"' if rowspan > 1 else "") + (
                f' colspan="{colspan}"' if colspan > 1 else ""
            )
            cells.append(f"<td{attrs}>{html.escape(cell_text(text))}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    return "<table>" + "".join(out) + "</table>"


def fix_tex(core: str) -> str:
    """Light LaTeX repair so formulas render: ``\\cdot\\cdot`` -> ``\\cdots``, close open braces."""
    core = re.sub(r"\\cdot\s*\\cdot(?![a-zA-Z])", r"\\cdots", core)
    core = re.sub(r"\\([a-zA-Z])(?=[_^{(])", r"\\ \1", core)
    core = re.sub(r"\\\\\s*(max|min|sup|inf)(?=_)", r"\\;\\\1", core)
    depth = 0
    for k, char in enumerate(core):
        if char in "{}" and (k == 0 or core[k - 1] != "\\"):
            depth += 1 if char == "{" else -1
    return core + "}" * depth if depth > 0 else core


def is_formula(block: Block) -> bool:
    """Layout sometimes labels a display formula 'text'; the model still answers LaTeX."""
    return block.task == "formula" or (
        block.label == "text" and bool(LATEX_TEXT.search(block.text or ""))
    )


def block_markdown(block: Block) -> str:
    """One read block -> its Markdown (empty for images and blank reads)."""
    text = block.text or ""
    if block.label in IMAGE_LABELS or not text.strip():
        return ""
    if block.task == "table":
        return otsl_to_html(text) if "<nl>" in text or "cel>" in text else text
    if is_formula(block):
        core = fix_tex(re.sub(r"^\\\[|\\\]$", "", text.strip()).strip())
        return f"$$\n{core}\n$$"
    if block.label == "doc_title":
        return "# " + text.replace("\n", "<br>")
    if block.label == "paragraph_title":
        return "## " + text.replace("\n", "<br>")
    if block.label == "figure_title":
        return f"*{text}*"
    return text


def _vertical_overlap(a: Block, b: Block) -> float:
    shared = min(a.box[3], b.box[3]) - max(a.box[1], b.box[1])
    return shared / max(1.0, min(a.height, b.height))


def _apart(a: Block, b: Block) -> bool:
    return a.box[2] <= b.box[0] + 6 or b.box[2] <= a.box[0] + 6


def side_by_side_rows(blocks: list[Block]) -> list[list[Block]]:
    """Consecutive blocks (reading order) that sit side by side on the page -> one row."""
    rows: list[list[Block]] = []
    for block in blocks:
        if (
            rows
            and all(_apart(other, block) for other in rows[-1])
            and any(_vertical_overlap(other, block) > 0.5 for other in rows[-1])
        ):
            rows[-1].append(block)
        else:
            rows.append([block])
    for row in rows:
        row.sort(key=lambda b: b.box[0])
    return rows


def page_markdown(blocks: list[Block], *, keep_margins: bool = False) -> str:
    """Blocks of one page -> Markdown; rows of short side-by-side blocks share one table."""
    visible = [
        b for b in blocks if b.text is not None and (keep_margins or b.label not in MARGIN_LABELS)
    ]
    if not visible:
        return ""
    page_height = max(b.box[3] for b in visible) - min(b.box[1] for b in visible) or 1
    out: list[str] = []
    grid: list[list[Block]] = []

    def flush() -> None:
        if grid:
            rows = "".join(
                "<tr>"
                + "".join(
                    f"<td>{html.escape(b.text or '').replace(chr(10), '<br>')}</td>" for b in row
                )
                + "</tr>"
                for row in grid
            )
            out.append(f"<table>{rows}</table>")
            grid.clear()

    for row in side_by_side_rows(visible):
        small = len(row) > 1 and all(
            b.task == "ocr" and b.label not in IMAGE_LABELS and b.height < 0.12 * page_height
            for b in row
        )
        if small:
            if grid and len(grid[-1]) != len(row):
                flush()
            grid.append(row)
        else:
            flush()
            out.extend(block_markdown(b) for b in row)
    flush()
    return split_tables("\n\n".join(part for part in out if part))


def _table_columns(table: str) -> int:
    first = re.search(r"<tr>(.*?)</tr>", table, re.S)
    if not first:
        return 0
    spans = re.findall(r"<td(?:[^>]*colspan=\"(\d+)\")?[^>]*>", first.group(1))
    return sum(int(span or 1) for span in spans)


def join_page_tables(pages: list[str]) -> list[str]:
    """Append the rows of a table that continues at the top of the next page to the previous one.

    Joined only when page i ends with a table and page i+1 starts with a table of the same width,
    and the new page either repeats the header row (dropped) or starts with a data row.
    """
    out = list(pages)
    for i in range(len(out) - 1):
        tail, head = out[i].rstrip(), out[i + 1].lstrip()
        if not (tail.endswith("</table>") and head.startswith("<table>")):
            continue
        last = tail[tail.rfind("<table>") :]
        first = head[: head.find("</table>") + len("</table>")]
        columns = _table_columns(last)
        if columns == 0 or columns != _table_columns(first):
            continue
        rows_a = re.findall(r"<tr>.*?</tr>", last, re.S)
        rows_b = re.findall(r"<tr>.*?</tr>", first, re.S)
        if not rows_a or not rows_b:
            continue
        if rows_b[0] == rows_a[0]:
            rows_b = rows_b[1:]
        else:
            first_cell = re.search(r"<td[^>]*>(.*?)</td>", rows_b[0], re.S)
            if not first_cell or not DATA_ROW_START.match(html.unescape(first_cell.group(1))):
                continue
        out[i] = tail[: tail.rfind("<table>")] + "<table>" + "".join(rows_a + rows_b) + "</table>"
        out[i + 1] = head[len(first) :].lstrip()
    return out
