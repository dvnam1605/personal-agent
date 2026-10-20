"""Deterministic Vietnamese answers for metadata results (no LLM).

The count and the list come straight from SQL, so the numbers shown are
exactly the rows that matched — each listed document is also returned as a
document-level citation the UI can open.
"""

from __future__ import annotations

from datetime import date

from app.domain.models.retrieval.citation import Citation
from app.domain.models.retrieval.metadata_filter import (
    MetadataFilterSpec,
    MetadataOperation,
    StructuredResult,
)
from app.services.ingestion.profiling.categories import DocCategory

MAX_LISTED = 15

_CATEGORY_NOUN = {
    DocCategory.ADMINISTRATIVE.value: "văn bản hành chính",
    DocCategory.REPORT.value: "báo cáo/tờ trình",
    DocCategory.CONTRACT.value: "hợp đồng",
    DocCategory.MINUTES.value: "biên bản",
    DocCategory.TECHNICAL.value: "tài liệu kỹ thuật",
    DocCategory.ACADEMIC.value: "đồ án/luận văn",
    DocCategory.SPREADSHEET.value: "bảng tính",
    DocCategory.OTHER.value: "tài liệu khác",
}


def describe_noun(spec: MetadataFilterSpec) -> str:
    if spec.subtypes:
        return " / ".join(s.lower() for s in spec.subtypes)
    if spec.categories:
        return " / ".join(_CATEGORY_NOUN.get(c, c) for c in spec.categories)
    return "tài liệu"


def describe_filters(spec: MetadataFilterSpec) -> str:
    return (" " + ", ".join(spec.descriptions)) if spec.descriptions else ""


def _fmt_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value).strftime("%d/%m/%Y")
    except ValueError:
        return value


def _line(idx: int, row: dict) -> str:
    title = row.get("title") or row.get("file_name") or row["document_id"]
    bits = []
    if row.get("doc_number"):
        bits.append(f"số {row['doc_number']}")
    if d := _fmt_date(row.get("doc_date")):
        bits.append(f"ngày {d}")
    if row.get("issuer"):
        bits.append(str(row["issuer"]))
    suffix = f" — {', '.join(bits)}" if bits else ""
    return f"{idx}. {title}{suffix} [{idx}]"


def render_metadata_answer(
    spec: MetadataFilterSpec, result: StructuredResult
) -> tuple[str, list[Citation]]:
    noun = describe_noun(spec)
    filters = describe_filters(spec)
    rows = list(result.rows)[:MAX_LISTED]
    citations = [
        Citation(
            evidence_id=f"doc:{r['document_id']}",
            document_id=str(r["document_id"]),
            title=str(r.get("title") or r.get("file_name") or ""),
            source_type="document",
            citation_type="document",
            section_title=r.get("doc_subtype"),
        )
        for r in rows
    ]
    if result.total == 0:
        return (
            f"Không tìm thấy {noun} nào{filters} trong kho tài liệu.",
            [],
        )
    listing = "\n".join(_line(i, r) for i, r in enumerate(rows, start=1))
    if spec.operation is MetadataOperation.COUNT:
        head = f"Có **{result.total}** {noun}{filters}."
        more = f"\n\nDanh sách {len(rows)} {noun} mới nhất:\n{listing}" if rows else ""
        tail = (
            f"\n\n(Còn {result.total - len(rows)} {noun} khác, hãy thu hẹp điều kiện để xem.)"
            if result.total > len(rows)
            else ""
        )
        return head + more + tail, citations
    if spec.operation is MetadataOperation.LATEST:
        return f"{noun.capitalize()} mới nhất{filters}:\n{listing}", citations
    head = f"Tìm thấy **{result.total}** {noun}{filters}:"
    tail = (
        f"\n\n(Hiển thị {len(rows)}/{result.total}, hãy thu hẹp điều kiện để xem thêm.)"
        if result.total > len(rows)
        else ""
    )
    return f"{head}\n{listing}{tail}", citations
