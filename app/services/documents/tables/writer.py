"""Spreadsheet generation: a table filled from the source documents.

The planner proposes columns for the user to edit; then each source document is
processed independently (bounded concurrency). Metadata columns come straight
from ``document_profiles``; content columns are extracted by the LLM with cited
evidence, figures absent from that evidence are cleared, and a reviewer LLM
re-checks cells up to ``MAX_REVIEW_ROUNDS`` times. Cells still flagged are
cleared and marked for the user, never guessed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from app.core.llm import llm_json
from app.core.text import unaccent_vietnamese
from app.domain.models.retrieval import RetrievalMode, RetrievalQuery
from app.services.documents.writing.quality import _number_values, _significant
from app.services.documents.writing.review import MAX_REVIEW_ROUNDS

logger = logging.getLogger(__name__)

TABLE_MAX_SOURCES = 30
MAX_COLUMNS = 12
MAX_ROWS_PER_DOCUMENT = 50
CONCURRENCY = 3
TABLE_LLM_TIMEOUT = 90.0
EVIDENCE_TOKENS = 3500
# Documents this short are read in full so retrieval cannot drop list items.
FULL_TEXT_CHARS = 16000

# Columns that come straight from the document profile.
META_SOURCES: dict[str, str] = {
    "doc_number": "Số hiệu văn bản",
    "doc_date": "Ngày ban hành",
    "title": "Tên / trích yếu văn bản",
    "issuer": "Cơ quan ban hành",
    "doc_type": "Loại văn bản",
    "signer": "Người ký",
}
CONTENT = "content"


class TableColumn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    source: str = CONTENT  # CONTENT or a META_SOURCES key


DEFAULT_COLUMNS = (
    TableColumn(name="Số hiệu", description="Số, ký hiệu văn bản", source="doc_number"),
    TableColumn(name="Ngày ban hành", description="Ngày ký ban hành", source="doc_date"),
    TableColumn(name="Trích yếu", description="Tên / trích yếu văn bản", source="title"),
    TableColumn(name="Nội dung chính", description="Nội dung chính của văn bản", source=CONTENT),
)


def normalize_columns(raw: Any) -> list[TableColumn]:
    """Valid, de-duplicated columns from LLM/user input (STT / Nguồn are added on render)."""
    out: list[TableColumn] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, TableColumn):
            item = item.model_dump()
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:80]
        key = unaccent_vietnamese(name).lower()
        if not name or key in seen or key in {"stt", "so thu tu", "nguon", "tai lieu nguon"}:
            continue
        source = str(item.get("source") or CONTENT).strip()
        seen.add(key)
        out.append(
            TableColumn(
                name=name,
                description=str(item.get("description") or "").strip()[:300],
                source=source if source in META_SOURCES else CONTENT,
            )
        )
    return out[:MAX_COLUMNS]


COLUMNS_SYSTEM_PROMPT = (
    "Bạn thiết kế một bảng tổng hợp (Excel) lập từ các văn bản nội bộ tiếng Việt.\n"
    "1. Đề xuất 4–10 cột sát yêu cầu. Mỗi cột có 'source': một trong "
    + ", ".join(f"{k} ({v})" for k, v in META_SOURCES.items())
    + " — các cột này lấy từ thông tin văn bản nên luôn chính xác, ưu tiên dùng khi phù hợp; "
    "hoặc 'content' khi phải trích từ nội dung văn bản.\n"
    "2. Xác định 'row_unit': mỗi dòng là gì (ví dụ 'văn bản', 'tập thể được khen thưởng', "
    "'nhiệm vụ'), và 'multi_rows': true nếu một văn bản có thể sinh nhiều dòng.\n"
    "3. Không thêm cột STT hay cột Nguồn (hệ thống tự thêm). Tên cột ngắn gọn, mô tả nói rõ "
    "cần ghi gì.\n"
    'Chỉ trả về JSON: {"title": "...", "row_unit": "...", "multi_rows": false, '
    '"columns": [{"name": "...", "description": "...", "source": "..."}]}'
)


async def propose_table(
    request: str,
    titles: list[str],
    snippets: list[str],
    *,
    json_call: Callable[..., Awaitable[dict[str, Any] | None]] = llm_json,
) -> dict[str, Any] | None:
    user = (
        f"Yêu cầu: {request}\n\nVăn bản nguồn:\n"
        + ("\n".join(f"- {t}" for t in titles) or "(chưa xác định)")
        + "\n\nTrích đoạn nội dung:\n"
        + ("\n".join(f"- {s}" for s in snippets) or "(không có)")
    )
    return await json_call(COLUMNS_SYSTEM_PROMPT, user, timeout=TABLE_LLM_TIMEOUT)


EXTRACT_SYSTEM_PROMPT = (
    "Bạn trích dữ liệu từ MỘT văn bản để điền vào bảng tổng hợp.\n"
    "1. Chỉ dùng thông tin trong các bằng chứng [1], [2], … của văn bản này. Số liệu, số tiền, "
    "ngày tháng, số hiệu, tên người, tên đơn vị chép NGUYÊN VĂN; không tự tính, không làm tròn.\n"
    '2. Ô không có thông tin trong bằng chứng thì để "v": "" — tuyệt đối không suy đoán.\n'
    '3. Mỗi ô ghi "src" là danh sách số bằng chứng chứa thông tin đó.\n'
    "4. Giá trị ngắn gọn (cụm từ hoặc 1–2 câu), không chứa nhãn [n].\n"
    '5. Nếu văn bản không liên quan đến nội dung của bảng, trả về {"rows": []}.\n'
    'Chỉ trả về JSON: {"rows": [{"c1": {"v": "...", "src": [1]}, "c2": {...}}]}'
)

REVIEW_SYSTEM_PROMPT = (
    "Bạn thẩm định dữ liệu đã điền vào bảng từ MỘT văn bản. Với từng ô (mã r<dòng>c<cột>), "
    "so giá trị với các bằng chứng được dẫn và với ý nghĩa của cột. Lỗi gồm:\n"
    "- contradiction: sai lệch với bằng chứng (số liệu, ngày, số hiệu, tên, đơn vị…);\n"
    "- unsupported: bằng chứng không có thông tin này;\n"
    "- wrong_citation: bằng chứng được dẫn không chứa thông tin này;\n"
    "- wrong_column: giá trị không đúng nội dung cột yêu cầu;\n"
    '- missing_row: (cell để "") văn bản còn đối tượng thuộc bảng chưa có dòng nào.\n'
    "Ô rỗng không phải lỗi. Không bắt lỗi cách viết. Không báo lỗi khi không chắc chắn.\n"
    'Chỉ trả về JSON: {"issues": [{"cell": "r1c2", "problem": "...", "explanation": "..."}]}'
    ' hoặc {"issues": []}.'
)


class CellIssue(BaseModel):
    cell: str = ""
    problem: str
    explanation: str = ""


def _clean_value(value: Any) -> str:
    text = re.sub(r"\[\d+\]", "", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:1000]


def _check_numbers(value: str, cited_text: str) -> bool:
    """Every significant figure in the value occurs in the cited evidence."""
    return not (_significant(_number_values(value)) - _number_values(cited_text))


class _Evidence(BaseModel):
    number: int
    text: str
    document_id: str
    chunk_id: str | None = None
    evidence_id: str = ""
    title: str | None = None
    page_start: int | None = None
    section_title: str | None = None


def _parse_rows(
    data: dict[str, Any] | None, n_cols: int, evidence: dict[int, _Evidence]
) -> list[list[dict[str, Any]]] | None:
    """LLM rows → per content column {value, src}; figures not in their evidence cleared."""
    if data is None or not isinstance(data.get("rows"), list):
        return None
    rows: list[list[dict[str, Any]]] = []
    for raw in data["rows"][:MAX_ROWS_PER_DOCUMENT]:
        if not isinstance(raw, dict):
            continue
        cells: list[dict[str, Any]] = []
        for j in range(1, n_cols + 1):
            cell = raw.get(f"c{j}")
            if isinstance(cell, dict):
                value, src = _clean_value(cell.get("v")), cell.get("src") or []
            else:
                value, src = _clean_value(cell), []
            numbers = sorted(
                {int(s) for s in src if str(s).isdigit() and int(s) in evidence}
                if isinstance(src, list)
                else set()
            )
            entry: dict[str, Any] = {"value": value, "src": numbers, "status": "ok", "note": ""}
            if value:
                cited = " ".join(evidence[n].text for n in numbers) or " ".join(
                    e.text for e in evidence.values()
                )
                if not _check_numbers(value, cited):
                    entry.update(
                        value="", src=[], status="cleared", note="Số liệu không có trong văn bản"
                    )
            else:
                entry["status"] = "empty"
            cells.append(entry)
        if any(c["value"] for c in cells):
            rows.append(cells)
    return rows


class TableWriter:
    """Fills an approved table outline document by document."""

    def __init__(
        self,
        pipeline: Any,
        *,
        json_call: Callable[..., Awaitable[dict[str, Any] | None]] = llm_json,
        review: bool = True,
    ) -> None:
        self._pipeline = pipeline
        self._json = json_call
        self._review = review

    async def write(
        self,
        outline: Any,
        request: str,
        requester_id: str | None,
        *,
        metadata: dict[str, dict[str, str]],
        full_texts: dict[str, list[dict[str, Any]]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Rows (with cells, citations, review) and the document-wide citation list.

        ``full_texts`` (top-level chunks per document) replaces retrieval for short documents.
        """
        full_texts = full_texts or {}
        columns = [TableColumn.model_validate(c) for c in outline.columns]
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def one(
            doc_id: str,
        ) -> tuple[str, list[list[dict[str, Any]]], list[_Evidence], dict[str, Any]]:
            async with semaphore:
                try:
                    return await self._document_rows(
                        doc_id,
                        columns,
                        outline,
                        request,
                        requester_id,
                        metadata.get(doc_id, {}),
                        full_texts.get(doc_id),
                    )
                except Exception:  # noqa: BLE001 - one document must not lose the table
                    logger.exception("table_document_failed document_id=%s", doc_id)
                    return doc_id, [], [], {"verdict": "failed"}

        results = await asyncio.gather(*(one(d) for d in outline.source_document_ids))

        citations: list[dict[str, Any]] = []
        number_by_key: dict[tuple[str, str], int] = {}

        def global_number(ev: _Evidence | None, doc_id: str, title: str | None) -> int:
            key = (doc_id, (ev.chunk_id or ev.evidence_id) if ev else "")
            if key not in number_by_key:
                number_by_key[key] = len(citations) + 1
                citations.append(
                    {
                        "number": number_by_key[key],
                        "document_id": doc_id,
                        "chunk_id": ev.chunk_id if ev else None,
                        "evidence_id": ev.evidence_id if ev else None,
                        "title": (ev.title if ev else None) or title,
                        "page_start": ev.page_start if ev else None,
                        "section_title": ev.section_title if ev else None,
                    }
                )
            return number_by_key[key]

        rows: list[dict[str, Any]] = []
        for doc_id, doc_rows, evidence, review in results:
            meta = metadata.get(doc_id, {})
            by_number = {e.number: e for e in evidence}
            for cells in doc_rows:
                out_cells: list[dict[str, Any]] = []
                row_numbers: set[int] = set()
                for cell in cells:
                    numbers = [
                        global_number(by_number[n], doc_id, meta.get("title"))
                        for n in cell.get("src", [])
                    ]
                    row_numbers.update(numbers)
                    out_cells.append(
                        {**{k: v for k, v in cell.items() if k != "src"}, "citations": numbers}
                    )
                if not row_numbers:  # metadata-only row: cite the document itself
                    row_numbers.add(global_number(None, doc_id, meta.get("title")))
                rows.append(
                    {
                        "document_id": doc_id,
                        "source_title": " — ".join(
                            p for p in (meta.get("doc_number"), meta.get("title")) if p
                        )
                        or doc_id,
                        "sort_key": meta.get("sort_key", ""),
                        "cells": out_cells,
                        "citations": sorted(row_numbers),
                        "review": review,
                    }
                )
        rows.sort(key=lambda r: r["sort_key"])
        return rows, citations

    async def _document_rows(
        self,
        doc_id: str,
        columns: list[TableColumn],
        outline: Any,
        request: str,
        requester_id: str | None,
        meta: dict[str, str],
        full_text: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[list[dict[str, Any]]], list[_Evidence], dict[str, Any]]:
        content_idx = [i for i, c in enumerate(columns) if c.source == CONTENT]

        def merge(content_rows: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
            merged: list[list[dict[str, Any]]] = []
            for content in content_rows:
                row: list[dict[str, Any]] = []
                it = iter(content)
                for col in columns:
                    if col.source == CONTENT:
                        row.append(next(it))
                    else:
                        value = meta.get(col.source, "")
                        row.append(
                            {
                                "value": value,
                                "src": [],
                                "status": "meta" if value else "empty",
                                "note": "",
                            }
                        )
                merged.append(row)
            return merged

        if not content_idx:
            return doc_id, merge([[]]), [], {"verdict": "metadata"}

        content_cols = [columns[i] for i in content_idx]
        evidence = _whole_document(doc_id, meta, full_text or []) or await self._evidence(
            doc_id, content_cols, request, requester_id
        )
        if not evidence:
            return doc_id, [], [], {"verdict": "no_evidence"}
        by_number = {e.number: e for e in evidence}

        rows = await self._extract(content_cols, outline, meta, by_number, None, None)
        if rows is None:
            return doc_id, [], evidence, {"verdict": "failed"}
        review: dict[str, Any] = {"verdict": "skipped", "rounds": 0, "issues": [], "cleared": []}
        if self._review and rows:
            rows, review = await self._review_loop(content_cols, outline, meta, by_number, rows)
        logger.info(
            "table_document_done document_id=%s rows=%d verdict=%s rounds=%s",
            doc_id,
            len(rows),
            review.get("verdict"),
            review.get("rounds"),
        )
        return doc_id, merge(rows), evidence, review

    async def _evidence(
        self, doc_id: str, columns: list[TableColumn], request: str, requester_id: str | None
    ) -> list[_Evidence]:
        query_text = " ".join(f"{c.name} {c.description}" for c in columns)
        bundle = await self._pipeline.run(
            RetrievalQuery(
                original_query=f"{request} {query_text}",
                search_query=f"{query_text} {request}",
                requester_id=requester_id,
                top_k_dense=20,
                top_k_sparse=20,
                mode=RetrievalMode.DOCUMENT_SEARCH,
                document_ids=[doc_id],
                context_token_budget=EVIDENCE_TOKENS,
            )
        )
        out: list[_Evidence] = []
        for item in bundle.items:
            text = str(getattr(item, "content_raw", "") or "").strip()
            if not text:
                continue
            anchors = getattr(item, "anchors", {}) or {}
            out.append(
                _Evidence(
                    number=len(out) + 1,
                    text=text[:2500],
                    document_id=item.document_id,
                    chunk_id=getattr(item, "primary_chunk_id", None),
                    evidence_id=str(getattr(item, "evidence_id", "") or ""),
                    title=anchors.get("profile_title") or getattr(item, "title", None),
                    page_start=anchors.get("page_start"),
                    section_title=anchors.get("section_title"),
                )
            )
        return out

    async def _extract(
        self,
        columns: list[TableColumn],
        outline: Any,
        meta: dict[str, str],
        evidence: dict[int, _Evidence],
        previous: list[list[dict[str, Any]]] | None,
        issues: list[CellIssue] | None,
    ) -> list[list[dict[str, Any]]] | None:
        unit = outline.row_unit or "văn bản"
        rows_rule = (
            f"Mỗi dòng là một {unit}; liệt kê ĐỦ mọi {unit} có trong văn bản."
            if outline.multi_rows
            else "Trả về đúng MỘT dòng cho văn bản này."
        )
        cols = "\n".join(
            f"- c{j}: {c.name} — {c.description or c.name}" for j, c in enumerate(columns, 1)
        )
        user = (
            f'Bảng: "{outline.title}". {rows_rule}\nCác cột cần điền:\n{cols}\n\n'
            f"Văn bản: {meta.get('title') or ''} {meta.get('doc_number') or ''}\n\n"
            "BẰNG CHỨNG:\n" + "\n\n".join(f"[{n}] {e.text}" for n, e in sorted(evidence.items()))
        )
        if previous is not None and issues:
            user += (
                "\n\nBẢN TRÍCH TRƯỚC (mã ô r<dòng>c<cột>):\n"
                + _rows_for_prompt(previous)
                + "\n\nCÁC LỖI CẦN SỬA (giữ nguyên các ô đúng, trả về đầy đủ các dòng):\n"
                + "\n".join(f"- {i.cell or 'chung'}: {i.problem} — {i.explanation}" for i in issues)
            )
        data = await self._json(
            EXTRACT_SYSTEM_PROMPT, user, timeout=TABLE_LLM_TIMEOUT, temperature=0.0
        )
        return _parse_rows(data, len(columns), evidence)

    async def _review_loop(
        self,
        columns: list[TableColumn],
        outline: Any,
        meta: dict[str, str],
        evidence: dict[int, _Evidence],
        rows: list[list[dict[str, Any]]],
    ) -> tuple[list[list[dict[str, Any]]], dict[str, Any]]:
        all_issues: list[CellIssue] = []
        last: list[CellIssue] = []
        rounds = 0
        while rounds < MAX_REVIEW_ROUNDS:
            issues = await self._find_issues(columns, evidence, rows)
            if issues is None:
                if rounds == 0:
                    return rows, {"verdict": "skipped", "rounds": 0, "issues": [], "cleared": []}
                break
            rounds += 1
            if not issues:
                verdict = "revised" if all_issues else "ok"
                return rows, {
                    "verdict": verdict,
                    "rounds": rounds,
                    "issues": [i.model_dump() for i in all_issues],
                    "cleared": [],
                }
            all_issues += issues
            last = issues
            if rounds == MAX_REVIEW_ROUNDS:
                break
            redone = await self._extract(columns, outline, meta, evidence, rows, issues)
            if not redone:
                break
            rows = redone
        cleared = _clear_cells(rows, last)
        return rows, {
            "verdict": "trimmed" if cleared else "revised",
            "rounds": rounds,
            "issues": [i.model_dump() for i in all_issues],
            "cleared": cleared,
        }

    async def _find_issues(
        self,
        columns: list[TableColumn],
        evidence: dict[int, _Evidence],
        rows: list[list[dict[str, Any]]],
    ) -> list[CellIssue] | None:
        cols = "\n".join(
            f"- c{j}: {c.name} — {c.description or c.name}" for j, c in enumerate(columns, 1)
        )
        user = (
            f"Các cột:\n{cols}\n\nBẰNG CHỨNG:\n"
            + "\n\n".join(f"[{n}] {e.text}" for n, e in sorted(evidence.items()))
            + "\n\nDỮ LIỆU ĐÃ ĐIỀN (mã ô: giá trị [bằng chứng dẫn]):\n"
            + _rows_for_prompt(rows)
        )
        data = await self._json(
            REVIEW_SYSTEM_PROMPT, user, timeout=TABLE_LLM_TIMEOUT, temperature=0.0
        )
        if data is None or not isinstance(data.get("issues"), list):
            return None
        issues: list[CellIssue] = []
        for raw in data["issues"]:
            if not isinstance(raw, dict) or not raw.get("problem"):
                continue
            issues.append(
                CellIssue(
                    cell=str(raw.get("cell") or "").strip().lower(),
                    problem=str(raw["problem"]).strip()[:40],
                    explanation=str(raw.get("explanation") or "").strip()[:400],
                )
            )
        return issues


def _whole_document(
    doc_id: str, meta: dict[str, str], chunks: list[dict[str, Any]]
) -> list[_Evidence]:
    """The document's own chunks as evidence when it is short enough to read in full."""
    chunks = [c for c in chunks if c.get("text")]
    if not chunks or sum(len(c["text"]) for c in chunks) > FULL_TEXT_CHARS:
        return []
    return [
        _Evidence(
            number=i,
            text=str(c["text"]),
            document_id=doc_id,
            chunk_id=c.get("chunk_id"),
            evidence_id=str(c.get("chunk_id") or ""),
            title=meta.get("title") or None,
            page_start=c.get("page_start"),
            section_title=c.get("section_title"),
        )
        for i, c in enumerate(chunks, 1)
    ]


def _rows_for_prompt(rows: list[list[dict[str, Any]]]) -> str:
    lines: list[str] = []
    for i, cells in enumerate(rows, 1):
        for j, cell in enumerate(cells, 1):
            if cell.get("value"):
                src = "".join(f"[{n}]" for n in cell.get("src", []))
                lines.append(f"r{i}c{j}: {cell['value']} {src}".rstrip())
    return "\n".join(lines) or "(trống)"


def _clear_cells(rows: list[list[dict[str, Any]]], issues: list[CellIssue]) -> list[dict[str, str]]:
    """Empty every cell the last review still flagged; returns what was cleared."""
    cleared: list[dict[str, str]] = []
    for issue in issues:
        m = re.fullmatch(r"r(\d+)c(\d+)", issue.cell)
        if not m:
            continue
        i, j = int(m.group(1)) - 1, int(m.group(2)) - 1
        if 0 <= i < len(rows) and 0 <= j < len(rows[i]) and rows[i][j].get("value"):
            cleared.append(
                {"cell": issue.cell, "value": rows[i][j]["value"], "reason": issue.explanation}
            )
            rows[i][j] = {
                "value": "",
                "src": [],
                "status": "cleared",
                "note": issue.explanation or "Chưa xác minh được với văn bản nguồn",
            }
    return cleared
