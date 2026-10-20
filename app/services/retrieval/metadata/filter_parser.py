"""Rule-based planner: decide the knowledge route and build a metadata filter.

Deterministic and LLM-free: it only fires the SQL route when the question
clearly asks for a count, a list or "the latest" document, or names a hard
selector (document number, person, date range) next to a content question.
Everything else stays on corpus RAG, so a misparse can never hide content.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from app.core.text import unaccent_vietnamese
from app.domain.models.retrieval.metadata_filter import (
    KnowledgeIntent,
    KnowledgePlan,
    MetadataFilterSpec,
    MetadataOperation,
)
from app.services.ingestion.profiling.categories import CATEGORY_SPECS, DocCategory

_GENERIC_NOUNS = ("van ban", "tai lieu", "ho so", "file", "giay to")


@dataclass(frozen=True)
class _Synonym:
    folded: str
    category: DocCategory
    subtype: str | None
    display: str


def _build_synonyms() -> list[_Synonym]:
    out: list[_Synonym] = []
    for spec in CATEGORY_SPECS:
        labels = {unaccent_vietnamese(s.label): s.label for s in spec.subtypes}
        for syn in spec.query_synonyms:
            folded = unaccent_vietnamese(syn)
            out.append(_Synonym(folded, spec.category, labels.get(folded), syn))
    # Longest first so "phụ lục hợp đồng" wins over "hợp đồng".
    return sorted(out, key=lambda s: len(s.folded), reverse=True)


_SYNONYMS = _build_synonyms()
DOC_NOUN_PATTERN = _DOC_NOUN = "|".join(
    re.escape(n)
    for n in sorted({*_GENERIC_NOUNS, *(s.folded for s in _SYNONYMS)}, key=len, reverse=True)
)
_COUNT = re.compile(
    rf"\b(?:bao nhieu|may|so luong|tong so|dem)\s+(?:\w+\s+){{0,2}}?(?:{_DOC_NOUN})\b"
)
_LIST = re.compile(
    rf"\b(?:liet ke|danh sach|ke ten|ke ra|cac|nhung|tat ca)\s+(?:\w+\s+){{0,2}}?(?:{_DOC_NOUN})\b"
)
_LATEST = re.compile(
    rf"(?:{_DOC_NOUN}).{{0,40}}\b(?:moi nhat|gan nhat|gan day nhat|cuoi cung)\b"
    rf"|\b(?:moi nhat|gan nhat|gan day nhat)\b.{{0,20}}(?:{_DOC_NOUN})"
)
_CONTENT = re.compile(
    r"\b(?:tom tat|tom luoc|noi dung|noi gi|ve gi|quy dinh gi|quy dinh nhung gi|so sanh|tong hop|"
    r"trinh bay|giai thich|phan tich|chi tiet|y chinh|ket luan|de xuat gi|kien nghi gi|"
    r"bao gom nhung|gom nhung gi|nhu the nao|ra sao)\b"
)
_DOC_NUMBER = re.compile(r"\b(\d{1,5}\s*[/\-]\s*(?:\d{4}\s*/\s*)?[A-Za-zĐđ][A-Za-zĐđ0-9\-/]*)")
_BARE_NUMBER = re.compile(
    rf"\b(?:{_DOC_NOUN})\s+(?:so\s+)?(\d{{1,5}})\b(?!\s*(?:nam|thang|ngay|/|-))"
)
_PERSON_BY = re.compile(
    r"\b(?:do|cua|boi|nguoi ky la|ky boi)\s+(?:ong|ba|anh|chi|dong chi|thay|co)?\s*"
    r"([A-ZĐÀ-Ỹ][\wÀ-ỹ]*(?:\s+[A-ZĐÀ-Ỹ][\wÀ-ỹ]*){1,4})"
)


def _year_range(year: int) -> tuple[date, date]:
    return date(year, 1, 1), date(year, 12, 31)


def _month_range(year: int, month: int) -> tuple[date, date]:
    if month == 12:
        return date(year, 12, 1), date(year, 12, 31)
    nxt = date(year, month + 1, 1)
    return date(year, month, 1), date.fromordinal(nxt.toordinal() - 1)


_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}


def parse_date_range(folded: str, today: date) -> tuple[date | None, date | None, str | None]:
    """``(from, to, description)`` for the first date expression in ``folded``."""
    m = re.search(
        r"\btu (?:ngay )?(\d{1,2})/(\d{1,2})/(\d{4}) den (?:ngay )?(\d{1,2})/(\d{1,2})/(\d{4})",
        folded,
    )
    if m:
        d1 = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        d2 = date(int(m.group(6)), int(m.group(5)), int(m.group(4)))
        return d1, d2, f"từ {d1:%d/%m/%Y} đến {d2:%d/%m/%Y}"
    m = re.search(r"\bquy\s+(\d|i{1,3}|iv)\b(?:\s*(?:/|nam)\s*(\d{4}))?", folded)
    if m:
        q = _ROMAN.get(m.group(1), None) or int(m.group(1))
        if 1 <= q <= 4:
            year = int(m.group(2)) if m.group(2) else today.year
            start = date(year, 3 * q - 2, 1)
            end = _month_range(year, 3 * q)[1]
            return start, end, f"quý {q}/{year}"
    m = re.search(r"\bthang\s+(\d{1,2})\s*(?:/|nam)\s*(\d{4})\b", folded)
    if m and 1 <= int(m.group(1)) <= 12:
        s, e = _month_range(int(m.group(2)), int(m.group(1)))
        return s, e, f"tháng {int(m.group(1))}/{m.group(2)}"
    if re.search(r"\bthang nay\b", folded):
        s, e = _month_range(today.year, today.month)
        return s, e, "tháng này"
    if re.search(r"\bthang truoc\b", folded):
        prev = date.fromordinal(date(today.year, today.month, 1).toordinal() - 1)
        s, e = _month_range(prev.year, prev.month)
        return s, e, "tháng trước"
    if re.search(r"\bnam nay\b", folded):
        s, e = _year_range(today.year)
        return s, e, "năm nay"
    if re.search(r"\bnam (?:ngoai|truoc)\b", folded):
        s, e = _year_range(today.year - 1)
        return s, e, "năm ngoái"
    m = re.search(r"\b(?:truoc|truoc ngay)\s+(\d{1,2})/(\d{1,2})/(\d{4})", folded)
    if m:
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return None, d, f"trước {d:%d/%m/%Y}"
    m = re.search(r"\b(?:sau|tu|ke tu)\s+(?:ngay\s+)?(\d{1,2})/(\d{1,2})/(\d{4})", folded)
    if m:
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return d, None, f"từ {d:%d/%m/%Y}"
    m = re.search(r"\b(?:nam|trong nam)\s+(19\d\d|20\d\d)\b", folded) or re.search(
        r"\b(19\d\d|20\d\d)\b", folded
    )
    if m:
        s, e = _year_range(int(m.group(1)))
        return s, e, f"năm {m.group(1)}"
    return None, None, None


def _match_known(folded: str, names: Iterable[tuple[str, str]]) -> str | None:
    """Longest known (folded, display) name appearing in the question."""
    best: tuple[int, str] | None = None
    for norm, display in names:
        if len(norm) >= 5 and re.search(rf"\b{re.escape(norm)}\b", folded):
            if best is None or len(norm) > best[0]:
                best = (len(norm), display)
    return best[1] if best else None


class KnowledgeQueryPlanner:
    """Turns a question into a :class:`KnowledgePlan` (no LLM, no DB)."""

    def plan(
        self,
        question: str,
        *,
        today: date | None = None,
        known_people: Iterable[tuple[str, str]] = (),
        known_issuers: Iterable[tuple[str, str]] = (),
    ) -> KnowledgePlan:
        today = today or date.today()
        folded = unaccent_vietnamese(question)
        descriptions: list[str] = []

        categories: list[str] = []
        subtypes: list[str] = []
        consumed = folded
        for syn in _SYNONYMS:
            if re.search(rf"\b{re.escape(syn.folded)}\b", consumed):
                if syn.category.value not in categories:
                    categories.append(syn.category.value)
                if syn.subtype and syn.subtype not in subtypes:
                    subtypes.append(syn.subtype)
                consumed = re.sub(rf"\b{re.escape(syn.folded)}\b", " ", consumed)

        if _COUNT.search(folded):
            operation = MetadataOperation.COUNT
        elif _LATEST.search(folded):
            operation = MetadataOperation.LATEST
        elif _LIST.search(folded) and not _CONTENT.search(folded):
            operation = MetadataOperation.LIST
        else:
            operation = None

        date_from, date_to, date_desc = parse_date_range(folded, today)
        if date_desc:
            descriptions.append(date_desc)

        doc_number = None
        m = _DOC_NUMBER.search(question)
        if m:
            doc_number = re.sub(r"\s+", "", m.group(1))
        else:
            mb = _BARE_NUMBER.search(folded)
            if mb and not re.fullmatch(r"(19|20)\d\d", mb.group(1)):
                doc_number = mb.group(1)
        if doc_number:
            descriptions.append(f"số {doc_number}")

        person = _match_known(folded, known_people)
        if person is None:
            mp = _PERSON_BY.search(question)
            if mp and len(mp.group(1).split()) >= 2:
                person = mp.group(1)
        if person:
            descriptions.append(f"liên quan {person}")
        issuer = _match_known(folded, known_issuers)
        if issuer:
            descriptions.append(f"của {issuer}")

        spec = MetadataFilterSpec(
            operation=operation or MetadataOperation.LIST,
            categories=tuple(categories),
            subtypes=tuple(subtypes),
            date_from=date_from,
            date_to=date_to,
            issuer=issuer,
            person=person,
            doc_number=doc_number,
            limit=1 if operation is MetadataOperation.LATEST else 20,
            descriptions=tuple(descriptions),
        )

        is_content = bool(_CONTENT.search(folded))
        if operation is not None and not is_content:
            return KnowledgePlan(intent=KnowledgeIntent.METADATA, spec=spec, reason=operation.value)
        if spec.has_selector and (is_content or operation is MetadataOperation.LATEST):
            return KnowledgePlan(
                intent=KnowledgeIntent.METADATA_THEN_CONTENT, spec=spec, reason="selector+content"
            )
        if doc_number and (categories or is_content):
            return KnowledgePlan(
                intent=KnowledgeIntent.METADATA_THEN_CONTENT, spec=spec, reason="doc_number"
            )
        return KnowledgePlan(intent=KnowledgeIntent.CONTENT, spec=spec, reason="content")
