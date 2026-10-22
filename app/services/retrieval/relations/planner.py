"""Rule-based planner for relation questions (no LLM, no DB).

Fires only when the question names a relation ("thay thế", "căn cứ", "ký",
"Bằng khen", "điều động"…) *and* either anchors it (a document number, a
known person/unit) or asks for a list/count/"ai". Everything else returns
``None`` and keeps the existing metadata / RAG routes.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date

from app.core.text import unaccent_vietnamese
from app.services.ingestion.relations.types import RelationType
from app.services.retrieval.metadata.filter_parser import (
    DOC_NOUN_PATTERN,
    parse_date_range,
)

_TRIGGERS: tuple[tuple[RelationType, re.Pattern[str]], ...] = (
    (RelationType.REPLACES, re.compile(r"\bthay the\b")),
    (RelationType.AMENDS, re.compile(r"\bsua doi\b")),
    (RelationType.BASED_ON, re.compile(r"\bcan cu\b")),
    (RelationType.PROPOSED_BY, re.compile(r"\bto trinh\b|\b(?:theo|xet) de nghi\b")),
    (RelationType.FUNDED_BY, re.compile(r"\bkinh phi\b")),
    (
        RelationType.SIGNED_BY,
        re.compile(
            r"\b(?:ai|nguoi|da|duoc)\s+ky\b|\bky\s+(?:cac|nhung|nhieu|bao nhieu|van ban|quyet dinh)\b"
            r"|\bky\s*(?:\?|$)"
        ),
    ),
    (RelationType.AWARDS, re.compile(r"\b(?:bang|giay) khen\b|\b(?:duoc|tang) khen\b")),
    (RelationType.RECOGNIZES, re.compile(r"\bdanh hieu\b|\bchien si thi dua\b")),
    (RelationType.APPOINTS, re.compile(r"\bbo nhiem\b")),
    (RelationType.TRANSFERS, re.compile(r"\bdieu dong\b|\bluan chuyen\b")),
    (RelationType.DISMISSES, re.compile(r"\bthoi giu chuc\b|\bmien nhiem\b")),
    (RelationType.TERMINATES, re.compile(r"\bcham dut hop dong\b|\bnghi huu\b")),
    (RelationType.RECRUITS, re.compile(r"\btuyen dung\b")),
)
# Without an anchor, fire only for lists/counts/"ai …"; a single document named
# by its subject is left to RAG.
_LIST_ASK = re.compile(
    r"\b(?:ai|bao nhieu|may|liet ke|tat ca|ke ten|danh sach)\b"
    rf"|\b(?:nhung|cac)\s+(?:{DOC_NOUN_PATTERN})\b"
)
_COUNT = re.compile(r"\b(?:bao nhieu|may|so luong|tong so)\b")
# "<văn bản> nào … thay thế" asks for the documents that hold the relation.
_SOURCE_QUESTION = re.compile(rf"\b(?:{DOC_NOUN_PATTERN})\s+(?:\w+\s+){{0,2}}?nao\b")
_DOC_NUMBER = re.compile(r"\b(\d{1,5}\s*[/\-]\s*(?:\d{4}\s*/\s*)?[A-Za-zĐđ][A-Za-zĐđ0-9\-/&]*)")
_BARE_NUMBER = re.compile(
    rf"\b(?:{DOC_NOUN_PATTERN}|nghi dinh|thong tu|to trinh)\s+(?:so\s+)?(\d{{1,5}})\b"
    r"(?!\s*(?:nam|thang|ngay))"
)


@dataclass(frozen=True)
class NumberAnchor:
    raw: str
    is_source: bool  # True: the question is about this document's own relations


@dataclass(frozen=True)
class RelationPlan:
    types: tuple[RelationType, ...]
    numbers: tuple[NumberAnchor, ...] = ()
    names: tuple[str, ...] = ()  # folded target names (people / units)
    date_from: date | None = None
    date_to: date | None = None
    count: bool = False
    descriptions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def anchored(self) -> bool:
        return bool(self.numbers or self.names)


def detect_types(folded: str) -> tuple[RelationType, ...]:
    return tuple(rel for rel, pattern in _TRIGGERS if pattern.search(folded))


def question_numbers(question: str, folded: str) -> list[str]:
    numbers = [re.sub(r"\s+", "", m.group(1)) for m in _DOC_NUMBER.finditer(question)]
    if not numbers:
        numbers = [
            m.group(1)
            for m in _BARE_NUMBER.finditer(folded)
            if not re.fullmatch(r"(19|20)\d\d", m.group(1))
        ]
    return numbers


def match_names(folded: str, names: Iterable[str]) -> list[str]:
    """Known target names in the question, longest first, without overlaps."""
    found: list[tuple[int, int, str]] = []
    for norm in names:
        if len(norm) < 6 or " " not in norm:
            continue
        for m in re.finditer(rf"\b{re.escape(norm)}\b", folded):
            found.append((m.start(), m.end(), norm))
    found.sort(key=lambda f: f[1] - f[0], reverse=True)
    taken: list[tuple[int, int]] = []
    out: list[str] = []
    for start, end, norm in found:
        if any(start < e and s < end for s, e in taken):
            continue
        taken.append((start, end))
        if norm not in out:
            out.append(norm)
    return out


class RelationQueryPlanner:
    def plan(
        self,
        question: str,
        *,
        today: date | None = None,
        known_names: Iterable[str] = (),
        is_source_number: Callable[[str], bool] = lambda raw: False,
    ) -> RelationPlan | None:
        folded = unaccent_vietnamese(question)
        types = detect_types(folded)
        if not types:
            return None

        first_trigger = min(m.start() for _, p in _TRIGGERS if (m := p.search(folded)) is not None)
        source_question = bool((m := _SOURCE_QUESTION.search(folded)) and m.start() < first_trigger)
        numbers: list[NumberAnchor] = []
        for raw in question_numbers(question, folded):
            own = is_source_number(raw)
            numbers.append(NumberAnchor(raw, is_source=own and not source_question))

        names = match_names(folded, known_names)
        if not (numbers or names or _LIST_ASK.search(folded)):
            return None

        # With a document number, a date describes that document, not a filter.
        date_from = date_to = date_desc = None
        if not numbers:
            date_from, date_to, date_desc = parse_date_range(folded, today or date.today())
        descriptions = tuple(d for d in (date_desc,) if d)
        return RelationPlan(
            types=types,
            numbers=tuple(numbers),
            names=tuple(names),
            date_from=date_from,
            date_to=date_to,
            count=bool(_COUNT.search(folded)),
            descriptions=descriptions,
        )
