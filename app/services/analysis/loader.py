"""Read a spreadsheet from Drive into rows: Google Sheets as CSV, Excel with openpyxl."""

from __future__ import annotations

import csv
import io
from itertools import islice
from typing import Any

from openpyxl import load_workbook

GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV = "text/csv"
MAX_ROWS = 5000
MAX_COLUMNS = 60


def spreadsheet_query(name_hint: str) -> str:
    """Drive search for a spreadsheet whose name contains ``name_hint``."""
    safe = name_hint.replace("\\", " ").replace("'", " ").strip()[:100]
    kinds = " or ".join(f"mimeType = '{kind}'" for kind in (GOOGLE_SHEET, XLSX, CSV))
    return f"name contains '{safe}' and ({kinds})"


def rows_from_matrix(matrix: list[list[Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    """First non-empty row is the header; blank or duplicate headers get a position name."""
    cleaned = [row[:MAX_COLUMNS] for row in matrix if any(str(c or "").strip() for c in row)]
    if not cleaned:
        return [], []
    header: list[str] = []
    for index, cell in enumerate(cleaned[0]):
        name = str(cell or "").strip() or f"Cột {index + 1}"
        header.append(name if name not in header else f"{name} ({index + 1})")
    rows = [
        {header[i]: (row[i] if i < len(row) else None) for i in range(len(header))}
        for row in cleaned[1 : MAX_ROWS + 1]
    ]
    return header, rows


async def load_table(drive: Any, file: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Download (or export) ``file`` and return its header and rows."""
    if file.mime_type == GOOGLE_SHEET:
        exported = await drive.download_file(file.id, export_mime_type=CSV)
        text = exported.text_content or exported.content_bytes.decode("utf-8", errors="replace")
        return rows_from_matrix(list(csv.reader(io.StringIO(text))))
    downloaded = await drive.download_file(file.id)
    if file.mime_type == CSV:
        text = downloaded.text_content or downloaded.content_bytes.decode("utf-8", errors="replace")
        return rows_from_matrix(list(csv.reader(io.StringIO(text))))
    workbook = load_workbook(io.BytesIO(downloaded.content_bytes), read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        matrix = [list(row) for row in islice(sheet.iter_rows(values_only=True), MAX_ROWS + 1)]
    finally:
        workbook.close()
    return rows_from_matrix(matrix)
