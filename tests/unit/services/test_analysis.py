"""Unit tests for spreadsheet analysis: plan interpreter, loaders and the service."""

from __future__ import annotations

import io
from typing import Any

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.models.google.drive import DriveDownloadResult, DriveFile, DriveFilePage
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import User
from app.services.analysis import service as service_module
from app.services.analysis.loader import GOOGLE_SHEET, XLSX, load_table, rows_from_matrix
from app.services.analysis.plan import PlanError, QueryPlan, run_plan, to_number
from app.services.analysis.service import analyze
from app.services.routing.query import QueryOrchestrator

MATRIX = [
    ["Tháng", "Khu vực", "Doanh thu"],
    ["1", "Bắc", "1.200.000"],
    ["1", "Nam", "800.000"],
    ["2", "Bắc", "1.500.000"],
    ["2", "Nam", ""],
    ["", "", ""],
]
COLUMNS, ROWS = rows_from_matrix(MATRIX)


def _plan(**fields: Any) -> QueryPlan:
    """Plans arrive as model JSON; validate them the same way."""
    return QueryPlan.model_validate(fields)


def test_numbers() -> None:
    assert [
        to_number(v) for v in ["3.000 đ", "1.5", "2.500,75", "1,234,567", "12%", True, "x"]
    ] == [
        3000.0,
        1.5,
        2500.75,
        1234567.0,
        12.0,
        None,
        None,
    ]


def test_group_sum_sort_and_filters() -> None:
    plan = _plan(
        group_by=["khu vuc"],
        aggregates=[{"fn": "sum", "column": "Doanh thu"}, {"fn": "count", "column": "*"}],
        sort_by="sum(Doanh thu)",
    )
    header, table = run_plan(plan, COLUMNS, ROWS)
    assert header == ["Khu vực", "sum(Doanh thu)", "count(*)"]
    assert table == [["Bắc", 2700000.0, 2], ["Nam", 800000.0, 2]]

    filtered = _plan(
        filters=[
            {"column": "Tháng", "op": "=", "value": 2},
            {"column": "Khu vực", "op": "contains", "value": "bac"},
        ],
        select=["Khu vực", "Doanh thu"],
    )
    assert run_plan(filtered, COLUMNS, ROWS)[1] == [["Bắc", "1.500.000"]]
    over = _plan(
        filters=[{"column": "Doanh thu", "op": ">", "value": "1.000.000"}],
        aggregates=[{"fn": "avg", "column": "Doanh thu"}],
    )
    assert run_plan(over, COLUMNS, ROWS)[1] == [[1350000.0]]

    with pytest.raises(PlanError):
        run_plan(_plan(select=["Lợi nhuận"]), COLUMNS, ROWS)
    with pytest.raises(PlanError):
        run_plan(_plan(), COLUMNS, ROWS)
    with pytest.raises(PlanError):
        run_plan(_plan(aggregates=[{"fn": "sum", "column": "*"}]), COLUMNS, ROWS)
    assert _plan(limit="900").limit == 50 and _plan(limit="x").limit == 20


def test_rows_from_matrix_names_blank_and_duplicate_headers() -> None:
    columns, rows = rows_from_matrix([["A", "", "A"], [1, 2]])
    assert columns == ["A", "Cột 2", "A (3)"] and rows == [{"A": 1, "Cột 2": 2, "A (3)": None}]
    assert rows_from_matrix([]) == ([], [])


class FakeDrive:
    def __init__(
        self, file: DriveFile | None, content: bytes = b"", text: str | None = None
    ) -> None:
        self.file = file
        self.content = content
        self.text = text
        self.exports: list[str | None] = []

    async def search_files(self, query: str, page_size: int = 20) -> DriveFilePage:
        return DriveFilePage(items=[self.file] if self.file else [])

    async def download_file(
        self, file_id: str, export_mime_type: str | None = None
    ) -> DriveDownloadResult:
        self.exports.append(export_mime_type)
        return DriveDownloadResult(
            file_id=file_id,
            name="x",
            mime_type=export_mime_type or XLSX,
            original_mime_type=XLSX,
            size_bytes=len(self.content),
            content_bytes=self.content,
            text_content=self.text,
        )


def _xlsx() -> bytes:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    for row in MATRIX:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def test_loaders_read_google_sheets_and_excel() -> None:
    sheet = DriveFile(id="s", name="Doanh thu", mime_type=GOOGLE_SHEET)
    drive = FakeDrive(sheet, text="Tháng,Doanh thu\n1,100\n")
    assert await load_table(drive, sheet) == (
        ["Tháng", "Doanh thu"],
        [{"Tháng": "1", "Doanh thu": "100"}],
    )
    assert drive.exports == ["text/csv"]

    excel = DriveFile(id="x", name="Doanh thu.xlsx", mime_type=XLSX)
    columns, rows = await load_table(FakeDrive(excel, content=_xlsx()), excel)
    assert columns == COLUMNS and len(rows) == 4


async def test_analyze_plans_with_the_model_and_bounds_the_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompts: list[str] = []

    async def model(system: str, prompt: str, **_kwargs: Any) -> dict[str, Any]:
        prompts.append(prompt)
        return {
            "group_by": ["Khu vực"],
            "aggregates": [{"fn": "sum", "column": "Doanh thu"}],
            "sort_by": "sum(Doanh thu)",
        }

    monkeypatch.setattr(service_module, "llm_json", model)
    excel = DriveFile(id="x", name="Doanh thu.xlsx", mime_type=XLSX)
    result = await analyze(FakeDrive(excel, content=_xlsx()), "Doanh thu", "doanh thu theo khu vực")
    assert result.rows == [["Bắc", 2700000.0], ["Nam", 800000.0]]
    assert "| Bắc | 2.700.000 |" in result.message
    assert "<table_sample>" in prompts[0]

    async def bad(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"select": ["Không có"]}

    monkeypatch.setattr(service_module, "llm_json", bad)
    failed = await analyze(FakeDrive(excel, content=_xlsx()), "Doanh thu", "?")
    assert failed.message.startswith("Không phân tích được: Không có cột")

    async def none(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(service_module, "llm_json", none)
    assert "Chưa lập được" in (await analyze(FakeDrive(excel, content=_xlsx()), "x", "?")).message
    assert "Không thấy bảng tính" in (await analyze(FakeDrive(None), "x", "?")).message


async def test_chat_command_runs_the_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    async def model(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"aggregates": [{"fn": "count", "column": "*"}]}

    monkeypatch.setattr(service_module, "llm_json", model)
    excel = DriveFile(id="x", name="Doanh thu.xlsx", mime_type=XLSX)
    drive = FakeDrive(excel, content=_xlsx())

    async def drive_for_user(*_args: Any) -> FakeDrive:
        return drive

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add(User(id="u", email="u@x.vn"))
        await session.flush()
        orchestrator = QueryOrchestrator(drive_for_user=drive_for_user)
        result = await orchestrator.handle(
            session, "u", "Phân tích file Doanh thu: có bao nhiêu dòng?"
        )
    await engine.dispose()
    assert result.data["analysis"]["rows"] == [[4]]
    assert "| count(*) |" in result.message
