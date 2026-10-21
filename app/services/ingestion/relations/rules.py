"""Deterministic relations from administrative document text.

Vietnamese administrative documents cite other documents in fixed phrasing
(legal basis, proposal, funding, replaces, amends), each with a document
number, so rules are cheaper and more exact than an LLM here.
"""

from __future__ import annotations

import re
from datetime import date

from app.core.text import unaccent_vietnamese
from app.services.ingestion.profiling.rules import normalize_doc_number
from app.services.ingestion.relations.types import RelationDraft, RelationType, TargetKind

MAX_TARGET_CHARS = 300

# Kinds of documents referenced in Vietnamese administrative text. OCR often
# drops the hook on "Nghị" ("Nghi định"), so both spellings are accepted.
_KIND = (
    r"Bộ\s+luật|Luật|Pháp\s+lệnh|Nghị\s+định|Nghi\s+định|Nghị\s+quyết|Quyết\s+định|"
    r"Quy\s+định|Quy\s+chế|Thông\s+tư(?:\s+liên\s+tịch)?|Chỉ\s+thị|Kết\s+luận|"
    r"Thông\s+báo(?:\s+kết\s+luận)?|Hướng\s+dẫn|Công\s+văn|Tờ\s+trình|Điều\s+lệ|Kế\s+hoạch"
)
_KIND_RE = re.compile(rf"(?<![\w])(?:{_KIND})(?![\w])")
_NUMBER = re.compile(
    r"(?<![\w/])(\d{1,5}\s*[/\-]\s*(?:\d{4}\s*/\s*)?[A-Za-zĐđ][\wĐđ&]*(?:\s*[/\-]\s*[\wĐđ&]+)*)"
)
_DATE_NUMERIC = re.compile(r"ngày\s+(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{4})")
_DATE_WORDS = re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})")
# A reference only counts when it starts the clause or follows a list connector;
# otherwise it merely describes the cited document.
_LEADING_OK = re.compile(r"(?:^|;|,|\bvà|\bCăn\s+cứ|\bđề\s+nghị\s+tại|\btại|\btheo)\s*$")
_BASIS_LINE = re.compile(r"^\s*[-–•*]?\s*Căn\s+cứ\b", re.IGNORECASE)
_PROPOSAL_LINE = re.compile(r"^\s*[-–•*]?\s*(?:Theo|Xét)\s+(?:đề\s+nghị|Tờ\s+trình)", re.IGNORECASE)
_PROPOSER = re.compile(r"đề\s+nghị\s+của\s+(.+?)\s+(?:tại|theo)\s+Tờ\s+trình", re.IGNORECASE)
_REPLACES = re.compile(r"thay\s+thế\s+(?:cho\s+)?", re.IGNORECASE)
_AMENDS = re.compile(
    r"sửa\s+đổi,?\s*(?:bổ\s+sung\s+)?(?:một\s+số\s+(?:điều|nội\s+dung)\s+(?:của|tại)\s+)?",
    re.IGNORECASE,
)
_FUNDING = re.compile(r"Kinh\s+phí", re.IGNORECASE)
_FUNDING_KIND = re.compile(r"Quyết\s+định\b")
# "Căn cứ Quy chế này" inside an attached regulation points at itself.
_SELF_REFERENCE = re.compile(rf"^(?:{_KIND})\s+này\b", re.IGNORECASE)
_SIGNATURE_BLOCK = re.compile(r"^\s*N[ơo]i\s+nh[ậa]n\b", re.IGNORECASE | re.MULTILINE)


def _parse_date(text: str) -> date | None:
    m = _DATE_NUMERIC.search(text) or _DATE_WORDS.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _clean(text: str) -> str:
    return " ".join(text.split()).strip(" ;,.:")


def _number_in_head(segment: str) -> str | None:
    """The document number in a reference's head (before " ngày" / " của")."""
    ends = [i for marker in (" ngày", " của ") if (i := segment.find(marker)) > 0]
    head = segment[: min(ends)] if ends else segment[:160]
    # The number follows "số" after the name, or else the kind directly.
    m = re.search(rf"\bsố\s*{_NUMBER.pattern}", head)
    if m is None:
        comma = head.find(",")
        m = _NUMBER.search(head[:comma] if comma > 0 else head)
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1)).rstrip("./-")


def document_target(segment: str, relation: RelationType, evidence: str) -> RelationDraft | None:
    text = _clean(segment)[:MAX_TARGET_CHARS]
    if not text or _SELF_REFERENCE.match(text):
        return None
    number = _number_in_head(text)
    return RelationDraft(
        relation_type=relation,
        target_kind=TargetKind.DOCUMENT,
        target_text=text,
        target_norm=unaccent_vietnamese(text),
        target_number_norm=normalize_doc_number(number) if number else None,
        target_date=_parse_date(text),
        evidence=_clean(evidence)[:600],
    )


def references(clause: str) -> list[str]:
    """Split one clause into the document references it lists."""
    parts: list[str] = []
    for part in re.split(r";", clause):
        starts = [
            m.start()
            for m in _KIND_RE.finditer(part)
            if _LEADING_OK.search(part[: m.start()].rstrip())
        ]
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(part)
            segment = part[start:end]
            if i + 1 < len(starts):
                # Drop the connector that introduced the next reference.
                segment = re.sub(r"(?:,|\bvà)\s*$", "", segment.rstrip())
            parts.append(segment)
    return parts


def _first_reference(text: str) -> str | None:
    m = _KIND_RE.search(text)
    if not m:
        return None
    tail = text[m.start() :]
    stop = re.search(r"[.;](?:\s|$)|\n", tail)
    return tail[: stop.start()] if stop else tail


def extract_rule_relations(text: str) -> list[RelationDraft]:
    """Relations to other documents stated in ``text`` (one document's full text)."""
    out: list[RelationDraft] = []
    body = text
    if m := _SIGNATURE_BLOCK.search(text):
        body = text[: m.start()]  # "Nơi nhận" lists recipients, never relations
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _BASIS_LINE.match(line):
            clause = re.sub(r"^\s*[-–•*]?\s*Căn\s+cứ\s*", "", line, flags=re.IGNORECASE)
            for ref in references(clause):
                if draft := document_target(ref, RelationType.BASED_ON, line):
                    out.append(draft)
            continue
        if _PROPOSAL_LINE.match(line):
            proposer = _PROPOSER.search(line)
            for m in re.finditer(r"Tờ\s+trình", line):
                ref = _first_reference(line[m.start() :])
                if ref and (draft := document_target(ref, RelationType.PROPOSED_BY, line)):
                    attrs = {"proposer": _clean(proposer.group(1))} if proposer else {}
                    out.append(draft.model_copy(update={"attributes": attrs}))
            continue
        for pattern, relation in (
            (_REPLACES, RelationType.REPLACES),
            (_AMENDS, RelationType.AMENDS),
        ):
            for m in pattern.finditer(line):
                after = line[m.end() :]
                if not _KIND_RE.match(after.lstrip()):
                    continue
                ref = _first_reference(after)
                if ref and (draft := document_target(ref, relation, line)):
                    if draft.target_number_norm:
                        out.append(draft)
        if _FUNDING.search(line):
            for m in re.finditer(r"(?:theo|tại)\s+", line, flags=re.IGNORECASE):
                after = line[m.end() :]
                if _FUNDING_KIND.match(after):
                    ref = _first_reference(after)
                    if ref and (draft := document_target(ref, RelationType.FUNDED_BY, line)):
                        if draft.target_number_norm:
                            out.append(draft)
    return dedupe(out)


def dedupe(drafts: list[RelationDraft]) -> list[RelationDraft]:
    seen: set[tuple[str, str]] = set()
    unique: list[RelationDraft] = []
    for d in drafts:
        if d.key() in seen:
            continue
        seen.add(d.key())
        unique.append(d)
    return unique
