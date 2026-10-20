"""Spreadsheet generation: column planning, per-document extraction, cell checks, .xlsx."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openpyxl import load_workbook

from app.services.documents.generation import DocumentOutline, DocumentPlanner
from app.services.documents.table import (
    DEFAULT_COLUMNS,
    TableColumn,
    TableWriter,
    normalize_columns,
)
from app.services.documents.table_render import render_table_markdown, render_table_xlsx
from app.services.documents.templates import is_generation_request, select_template

EVIDENCE = {
    "d1": [
        "Điều 1. Tặng Bằng khen cho 09 tập thể có thành tích xuất sắc tại Liên hoan.",
        "Điều 2. Mỗi tập thể được thưởng 1.500.000 đồng từ kinh phí Liên hoan.",
    ],
    "d2": ["Điều 1. Tặng Bằng khen cho Liên Chi hội Nhà báo vì thành tích năm 2025."],
}
META = {
    "d1": {
        "doc_number": "1119/QĐ-TNVN",
        "doc_date": "13/04/2026",
        "title": "QĐ 1119",
        "sort_key": "2026-04-13",
    },
    "d2": {
        "doc_number": "91/QĐ-TNVN",
        "doc_date": "16/01/2026",
        "title": "QĐ 91",
        "sort_key": "2026-01-16",
    },
}
COLUMNS = [
    TableColumn(name="Số hiệu", source="doc_number"),
    TableColumn(name="Đối tượng", description="Tập thể được khen"),
    TableColumn(name="Mức thưởng", description="Số tiền thưởng"),
]


class FakePipeline:
    async def run(self, query: Any) -> Any:
        doc = query.document_ids[0]
        items = [
            SimpleNamespace(
                content_raw=text,
                document_id=doc,
                primary_chunk_id=f"{doc}-c{i}",
                evidence_id=f"{doc}-e{i}",
                title=f"T-{doc}",
                anchors={"page_start": 1},
            )
            for i, text in enumerate(EVIDENCE[doc], 1)
        ]
        return SimpleNamespace(items=items)


class Replies:
    """Scripted LLM: extraction/review replies per document, in call order."""

    def __init__(self, script: dict[str, list[dict[str, Any] | None]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str, **_: Any) -> dict[str, Any] | None:
        doc = "d1" if "Liên hoan" in user else "d2"
        self.calls.append((doc, "review" if system.startswith("Bạn thẩm định") else "extract"))
        return self.script[doc].pop(0)


def _outline(**kw: Any) -> DocumentOutline:
    return DocumentOutline(
        template_key="table_summary",
        title="Bảng khen thưởng",
        kind="table",
        columns=COLUMNS,
        row_unit="quyết định",
        source_document_ids=["d1", "d2"],
        **kw,
    )


def test_table_requests_are_detected() -> None:
    q = "Lập bảng tổng hợp các quyết định khen thưởng năm 2026"
    assert is_generation_request(q) and select_template(q).key == "table_summary"
    assert select_template("Tạo file excel danh sách tập thể được khen").key == "table_summary"
    assert not is_generation_request("tặng bằng khen cho ai")


def test_normalize_columns_drops_stt_source_and_duplicates() -> None:
    cols = normalize_columns(
        [
            {"name": "STT"},
            {"name": "Số hiệu", "source": "doc_number"},
            {"name": "số hiệu"},
            {"name": "Nguồn"},
            {"name": "Ghi chú", "source": "made_up"},
        ]
    )
    assert [(c.name, c.source) for c in cols] == [("Số hiệu", "doc_number"), ("Ghi chú", "content")]


async def test_planner_without_llm_uses_default_columns() -> None:
    class Meta:
        async def plan(self, request: str, requester_id: Any) -> Any:
            from app.domain.models.retrieval.metadata_filter import (
                KnowledgeIntent,
                KnowledgePlan,
                MetadataFilterSpec,
            )

            return KnowledgePlan(
                intent=KnowledgeIntent.CONTENT,
                spec=MetadataFilterSpec(categories=("administrative",)),
            )

        async def resolve_document_ids(self, spec: Any, requester_id: Any, *, cap: int):
            return [f"d{i}" for i in range(20)], 20

    outline = await DocumentPlanner(Meta(), FakePipeline(), use_llm=False).plan(
        "Lập bảng tổng hợp các quyết định khen thưởng năm 2026", None
    )
    assert outline.kind == "table" and outline.columns == list(DEFAULT_COLUMNS)
    assert len(outline.source_document_ids) == 20  # tables read more documents than prose


async def test_cells_are_extracted_checked_and_merged_with_metadata() -> None:
    replies = Replies(
        {
            "d1": [
                {
                    "rows": [
                        {
                            "c1": {"v": "09 tập thể", "src": [1]},
                            "c2": {"v": "1.500.000 đồng", "src": [2]},
                        }
                    ]
                },
                {"issues": []},
            ],
            "d2": [
                # 3.000.000 occurs nowhere in d2's evidence → cleared without asking the LLM.
                {
                    "rows": [
                        {
                            "c1": {"v": "Liên Chi hội Nhà báo", "src": [1]},
                            "c2": {"v": "3.000.000 đồng", "src": [1]},
                        }
                    ]
                },
                {"issues": []},
            ],
        }
    )
    rows, citations = await TableWriter(FakePipeline(), json_call=replies).write(
        _outline(), "lập bảng", None, metadata=META
    )
    assert [r["document_id"] for r in rows] == ["d2", "d1"]  # sorted by date
    d2, d1 = rows
    assert [c["value"] for c in d1["cells"]] == ["1119/QĐ-TNVN", "09 tập thể", "1.500.000 đồng"]
    assert d1["cells"][0]["status"] == "meta"
    assert d2["cells"][2]["status"] == "cleared" and d2["cells"][2]["value"] == ""
    assert d1["review"]["verdict"] == "ok"
    cited = {c["number"]: c["chunk_id"] for c in citations}
    assert {cited[n] for n in d1["citations"]} == {"d1-c1", "d1-c2"}


async def test_flagged_cell_is_re_extracted_then_cleared_after_three_reviews() -> None:
    wrong = {
        "rows": [{"c1": {"v": "10 tập thể", "src": [1]}, "c2": {"v": "1.500.000 đồng", "src": [2]}}]
    }
    flag = {"issues": [{"cell": "r1c1", "problem": "contradiction", "explanation": "nguồn ghi 09"}]}
    replies = Replies(
        {
            "d1": [wrong, flag, wrong, flag, wrong, flag],
            "d2": [
                {
                    "rows": [
                        {
                            "c1": {"v": "Liên Chi hội Nhà báo", "src": [1]},
                            "c2": {"v": "", "src": []},
                        }
                    ]
                },
                {"issues": []},
            ],
        }
    )
    rows, _ = await TableWriter(FakePipeline(), json_call=replies).write(
        _outline(), "lập bảng", None, metadata=META
    )
    d1 = next(r for r in rows if r["document_id"] == "d1")
    assert [k for d, k in replies.calls if d == "d1"].count("review") == 3
    # "10" is not a figure > 10, so only the review can catch it; it is cleared.
    assert d1["cells"][1]["status"] == "cleared" and d1["cells"][1]["note"] == "nguồn ghi 09"
    assert d1["cells"][2]["value"] == "1.500.000 đồng"
    assert d1["review"]["verdict"] == "trimmed" and d1["review"]["rounds"] == 3


async def test_unavailable_llm_leaves_metadata_rows_only_for_failed_documents() -> None:
    replies = Replies({"d1": [None], "d2": [None]})
    rows, _ = await TableWriter(FakePipeline(), json_call=replies).write(
        _outline(), "lập bảng", None, metadata=META
    )
    assert rows == []


def test_render_markdown_and_xlsx(tmp_path: Path) -> None:
    columns = [c.model_dump() for c in COLUMNS]
    rows = [
        {
            "document_id": "d1",
            "cells": [
                {"value": "1119/QĐ-TNVN", "status": "meta"},
                {"value": "09 tập thể", "status": "ok"},
                {"value": "", "status": "cleared", "note": "nguồn ghi khác"},
            ],
            "citations": [1],
        }
    ]
    citations = [{"number": 1, "title": "QĐ 1119", "page_start": 1}]
    md = render_table_markdown("Bảng thử", columns, rows, citations)
    assert "| STT | Số hiệu | Đối tượng | Mức thưởng | Nguồn |" in md
    assert "| 1 | 1119/QĐ-TNVN | 09 tập thể | (chưa xác minh) | [1] |" in md

    path = render_table_xlsx(tmp_path / "t.xlsx", "Bảng thử", columns, rows, citations)
    wb = load_workbook(path)
    ws = wb["Bảng"]
    assert [c.value for c in ws[4]] == ["STT", "Số hiệu", "Đối tượng", "Mức thưởng", "Nguồn"]
    assert [c.value for c in ws[5]] == [1, "1119/QĐ-TNVN", "09 tập thể", None, "[1]"]
    assert ws["D5"].comment is not None and "nguồn ghi khác" in ws["D5"].comment.text
    assert wb["Nguồn"]["B2"].value == "QĐ 1119, tr. 1"


async def test_short_documents_are_read_in_full_instead_of_retrieved() -> None:
    class NoRetrieval:
        async def run(self, query: Any) -> Any:
            raise AssertionError("short documents must not be retrieved")

    seen: list[str] = []

    async def reply(system: str, user: str, **_: Any) -> dict[str, Any] | None:
        seen.append(user)
        if system.startswith("Bạn thẩm định"):
            return {"issues": []}
        return {"rows": [{"c1": {"v": "Văn phòng", "src": [2]}, "c2": {"v": "", "src": []}}]}

    full = {
        "d1": [
            {"chunk_id": "p0", "text": "Điều 1. Tặng Bằng khen cho các tập thể:"},
            {"chunk_id": "p1", "text": "Văn phòng; Ban Thư ký biên tập."},
        ]
    }
    outline = _outline().model_copy(update={"source_document_ids": ["d1"]})
    rows, citations = await TableWriter(NoRetrieval(), json_call=reply).write(
        outline, "lập bảng", None, metadata=META, full_texts=full
    )
    assert "[2] Văn phòng; Ban Thư ký biên tập." in seen[0]
    assert rows[0]["cells"][1]["value"] == "Văn phòng"
    assert citations[0]["chunk_id"] == "p1"
