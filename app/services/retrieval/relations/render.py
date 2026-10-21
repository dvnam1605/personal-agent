"""Deterministic Vietnamese answers for relation lookups (no LLM).

Each matching document becomes one numbered, cited line named the way people
cite it, followed by its matching relations as short clauses with details kept
verbatim. The text doubles as the fact draft an LLM may reword (verbalize.py).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any

from app.domain.models.retrieval.citation import Citation
from app.services.ingestion.relations.types import RelationType
from app.services.retrieval.relations.planner import RelationPlan

MAX_FACTS_PER_DOCUMENT = 15
MAX_TARGET_CHARS = 180


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


def _attr(attrs: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = attrs.get(key)
        if value:
            return _clip(str(value).strip())
    return ""


def _fact_text(fact: dict[str, Any]) -> str:
    """One relation as a clause: 'bổ nhiệm X giữ chức Y', 'căn cứ Nghị định …'."""
    relation = RelationType(fact["relation_type"])
    attrs = fact.get("attributes") or {}
    target = _clip(str(fact["target_text"]).strip())
    position = _attr(attrs, "to_position", "position")
    if relation is RelationType.APPOINTS:
        return f"bổ nhiệm {target}" + (f" giữ chức {position}" if position else "")
    if relation is RelationType.TRANSFERS:
        old = _attr(attrs, "from_position", "position")
        new = _attr(attrs, "to_position")
        return (
            f"điều động {target}"
            + (f" từ {old}" if old and old != new else "")
            + (f" đến {new}" if new else "")
        )
    if relation is RelationType.DISMISSES:
        old = _attr(attrs, "from_position", "position")
        return f"cho {target} thôi giữ chức" + (f" {old}" if old else " vụ")
    if relation is RelationType.TERMINATES:
        return f"chấm dứt hợp đồng làm việc với {target}" + (f" ({position})" if position else "")
    if relation is RelationType.RECRUITS:
        return f"tuyển dụng {target}" + (f" vào {position}" if position else "")
    if relation is RelationType.AWARDS:
        award = _attr(attrs, "award")
        reason = _attr(attrs, "reason")
        return (f"tặng {award} cho {target}" if award else f"khen thưởng {target}") + (
            f", vì {reason}" if reason else ""
        )
    if relation is RelationType.RECOGNIZES:
        title = _attr(attrs, "title")
        return f"công nhận danh hiệu {title} cho {target}" if title else f"công nhận {target}"
    if relation is RelationType.SIGNED_BY:
        role = _attr(attrs, "role")
        return f"do {target}" + (f" ({role})" if role else "") + " ký"
    if relation is RelationType.PROPOSED_BY:
        proposer = _attr(attrs, "proposer")
        return f"theo đề nghị tại {target}" + (f" của {proposer}" if proposer else "")
    phrase = {
        RelationType.REPLACES: "thay thế",
        RelationType.AMENDS: "sửa đổi, bổ sung",
        RelationType.BASED_ON: "căn cứ",
        RelationType.FUNDED_BY: "chi kinh phí theo",
    }.get(relation, relation.value)
    return f"{phrase} {target}"


_TITLE_SUBJECT = re.compile(r"(?i)\s+(về việc|về|v/v)\s+")


def _document_name(doc: dict[str, Any]) -> str:
    """'Quyết định về việc X' + number/date → 'Quyết định số N ngày D về việc X'."""
    title = str(doc.get("title") or "").strip()
    number = str(doc.get("doc_number") or "").strip()
    when = _fmt_date(doc.get("doc_date"))
    number_in_title = bool(number) and number.replace(" ", "") in title.replace(" ", "")
    ident = " ".join(
        p
        for p in (
            f"số {number}" if number and not number_in_title else "",
            f"ngày {when}" if when else "",
        )
        if p
    )
    if not ident:
        return title or str(doc["document_id"])
    m = _TITLE_SUBJECT.search(title)
    if m and m.start() > 0:
        return f"{title[: m.start()]} {ident} {title[m.start() :].strip()}"
    if title:
        return f"{title} ({ident})"
    return f"Văn bản {ident}"


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
    n = len(documents)
    head = f"Trong kho có {n} văn bản{filters}:" if n != 1 else f"Trong kho có 1 văn bản{filters}:"
    if plan.count:
        head = f"Có **{n}** văn bản{filters}:"

    lines: list[str] = []
    citations: list[Citation] = []
    for idx, doc in enumerate(documents, start=1):
        title = str(doc.get("title") or "")
        doc_facts = by_doc[str(doc["document_id"])]
        shown = [_fact_text(f) for f in doc_facts[:MAX_FACTS_PER_DOCUMENT]]
        if len(doc_facts) > MAX_FACTS_PER_DOCUMENT:
            shown.append(f"và {len(doc_facts) - MAX_FACTS_PER_DOCUMENT} nội dung khác")
        if len(shown) <= 2:
            lines.append(f"{idx}. {_document_name(doc)} [{idx}]: {'; '.join(shown)}.")
        else:
            lines.append(f"{idx}. {_document_name(doc)} [{idx}]:")
            lines.extend(f"   - {item}" for item in shown)
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
