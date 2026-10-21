"""Deterministic Vietnamese answers for relation lookups (no LLM).

Each matching document is one numbered line (and one document citation);
the relations that answer the question are listed under it, with the
stated details (award, position, proposer) kept verbatim.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from app.domain.models.retrieval.citation import Citation
from app.services.ingestion.relations.types import RelationType
from app.services.retrieval.relations.planner import RelationPlan

MAX_FACTS_PER_DOCUMENT = 15
MAX_TARGET_CHARS = 180

_LABEL = {
    RelationType.REPLACES: "thay thế",
    RelationType.AMENDS: "sửa đổi, bổ sung",
    RelationType.BASED_ON: "căn cứ",
    RelationType.PROPOSED_BY: "theo đề nghị tại",
    RelationType.FUNDED_BY: "kinh phí theo",
    RelationType.SIGNED_BY: "người ký",
    RelationType.AWARDS: "khen thưởng",
    RelationType.RECOGNIZES: "công nhận danh hiệu cho",
    RelationType.APPOINTS: "bổ nhiệm",
    RelationType.TRANSFERS: "điều động",
    RelationType.DISMISSES: "cho thôi giữ chức vụ",
    RelationType.TERMINATES: "chấm dứt hợp đồng với",
    RelationType.RECRUITS: "tuyển dụng",
}
_ATTRIBUTE_LABEL = {
    "award": "hình thức",
    "title": "danh hiệu",
    "position": "chức vụ/đơn vị",
    "from_position": "từ",
    "to_position": "sang",
    "reason": "thành tích/lý do",
    "proposer": "của",
    "role": "chức vụ",
}


def _fmt_date(value: Any) -> str | None:
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value).strftime("%d/%m/%Y")
        except ValueError:
            return value
    return None


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TARGET_CHARS else text[: MAX_TARGET_CHARS - 1] + "…"


def _fact_line(fact: dict[str, Any]) -> str:
    relation = RelationType(fact["relation_type"])
    attrs = fact.get("attributes") or {}
    details = "; ".join(
        f"{_ATTRIBUTE_LABEL.get(k, k)}: {v}"
        for k, v in attrs.items()
        if v and k in _ATTRIBUTE_LABEL
    )
    suffix = f" ({details})" if details else ""
    return f"   - {_LABEL[relation]}: {_clip(str(fact['target_text']))}{suffix}"


def render_relation_answer(
    plan: RelationPlan,
    documents: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    total: int,
) -> tuple[str, list[Citation]]:
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        by_doc[str(fact["document_id"])].append(fact)
    documents = [d for d in documents if str(d["document_id"]) in by_doc]

    filters = f" ({', '.join(plan.descriptions)})" if plan.descriptions else ""
    head = f"Tìm thấy **{len(documents)}** văn bản{filters} theo quan hệ đã lưu trong kho:"
    if plan.count:
        head = f"Có **{len(documents)}** văn bản{filters}:"

    lines: list[str] = []
    citations: list[Citation] = []
    for idx, doc in enumerate(documents, start=1):
        bits = [f"**{doc['doc_number']}**"] if doc.get("doc_number") else []
        if d := _fmt_date(doc.get("doc_date")):
            bits.append(f"ngày {d}")
        meta = ", ".join(bits)
        title = str(doc.get("title") or "")
        lines.append(f"{idx}. {meta} — {title} [{idx}]" if meta else f"{idx}. {title} [{idx}]")
        doc_facts = by_doc[str(doc["document_id"])]
        lines.extend(_fact_line(f) for f in doc_facts[:MAX_FACTS_PER_DOCUMENT])
        if len(doc_facts) > MAX_FACTS_PER_DOCUMENT:
            lines.append(f"   - … và {len(doc_facts) - MAX_FACTS_PER_DOCUMENT} quan hệ khác")
        citations.append(
            Citation(
                evidence_id=f"doc:{doc['document_id']}",
                document_id=str(doc["document_id"]),
                title=title,
                source_type="document",
                citation_type="document",
            )
        )
    tail = (
        f"\n\n(Hiển thị {len(documents)}/{total} văn bản, hãy thu hẹp điều kiện để xem thêm.)"
        if total > len(documents)
        else ""
    )
    return head + "\n" + "\n".join(lines) + tail, citations
