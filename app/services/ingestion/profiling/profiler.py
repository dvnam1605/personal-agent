"""Build a :class:`DocumentProfileDraft` for any document kind.

Pipeline: signals → rule classifier → generic label/date/number rules →
category-specific rules → (only if unsure) one LLM call → manual overrides.
Every field records where it came from (``field_sources``) so reviewers and
the evaluation harness can tell rule, LLM and human values apart.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.text import unaccent_vietnamese
from app.domain.models.ingestion.parsed_document import ParsedDocument
from app.services.ingestion.administrative_extractor import AdministrativeMetadataExtractor
from app.services.ingestion.profiling.categories import DocCategory, required_fields_for
from app.services.ingestion.profiling.classifier import Classification, RuleClassifier
from app.services.ingestion.profiling.extras import validate_extras
from app.services.ingestion.profiling.llm_extractor import extract_profile_with_llm
from app.services.ingestion.profiling.rules import (
    RuleFields,
    clean_person,
    detect_language,
    extract_doc_number,
    extract_issuer,
    label_values,
    normalize_doc_number,
    parse_date_any,
    person,
)
from app.services.ingestion.profiling.signals import (
    DocumentSignals,
    build_signals,
    build_signals_from_text,
)

EXTRACTOR_VERSION = "profile-v5"

_PERSON_KINDS = {"author", "signer", "supervisor", "party", "attendee", "chair"}
_TITLE_MAX_CHARS = 220
_REPLACES = re.compile(
    r"(?i)thay\s+thế\s+(?:Quyết\s+định|Chỉ\s+thị|Quy\s+chế|Quy\s+định|Thông\s+báo|văn\s+bản)?"
    r"\s*số\s*(\d{1,5}\s*/\s*[A-ZĐ][A-ZĐ0-9\-/]*)"
)
_PARTY_LINE = re.compile(r"(?i)^B[ÊE]N\s+([A-D])\s*(?:\([^)]*\))?\s*[:：]\s*(.+)$")
_ISBN = re.compile(r"(?i)\bISBN(?:-1[03])?\s*:?\s*([0-9][0-9\-\s]{8,20}[0-9Xx])")


class DocumentProfileDraft(BaseModel):
    """Everything persisted to ``document_profiles`` (minus keys/timestamps)."""

    doc_category: DocCategory = DocCategory.OTHER
    doc_subtype: str | None = None
    title: str | None = None
    doc_number: str | None = None
    doc_number_norm: str | None = None
    issuer: str | None = None
    issuer_norm: str | None = None
    subject: str | None = None
    summary: str | None = None
    language: str | None = None
    doc_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    entities: list[dict[str, str]] = Field(default_factory=list)
    entity_names_norm: str | None = None
    tags: list[str] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)
    field_sources: dict[str, str] = Field(default_factory=dict)
    extractor_version: str = EXTRACTOR_VERSION
    category_confidence: float = 0.0
    extraction_confidence: float = 0.0
    needs_review: bool = False
    review_reason: str | None = None
    input_hash: str | None = None

    def to_row(self) -> dict[str, Any]:
        row = self.model_dump()
        row["doc_category"] = self.doc_category.value
        return row


def profile_input_hash(signals: DocumentSignals) -> str:
    digest = hashlib.sha256()
    for part in (EXTRACTOR_VERSION, signals.filename, signals.header_text, signals.tail_text):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


class DocumentProfiler:
    """Stateless profiler; ``use_llm=False`` gives a fully deterministic run."""

    def __init__(self, *, use_llm: bool | None = None, classifier: RuleClassifier | None = None):
        self._use_llm = settings.profiling.llm_enabled if use_llm is None else use_llm
        self._classifier = classifier or RuleClassifier()

    async def aprofile(
        self, document: ParsedDocument, *, overrides: dict[str, Any] | None = None
    ) -> DocumentProfileDraft:
        return await self.aprofile_signals(build_signals(document), overrides=overrides)

    async def aprofile_text(
        self,
        text: str,
        filename: str,
        *,
        headings: tuple[str, ...] = (),
        overrides: dict[str, Any] | None = None,
    ) -> DocumentProfileDraft:
        signals = build_signals_from_text(text, filename, headings=headings)
        return await self.aprofile_signals(signals, overrides=overrides)

    async def aprofile_signals(
        self, signals: DocumentSignals, *, overrides: dict[str, Any] | None = None
    ) -> DocumentProfileDraft:
        cls = self._classifier.classify(signals)
        fields = self._apply_rules(signals, cls)
        draft = self._to_draft(signals, cls, fields)

        missing = self._missing_required(draft)
        unsure = cls.confidence < settings.profiling.llm_threshold
        if self._use_llm and (unsure or missing):
            llm = await extract_profile_with_llm(signals)
            if llm is None:
                draft.needs_review = True
                draft.review_reason = (
                    "llm_unavailable" if unsure else f"missing:{','.join(missing)}"
                )
            else:
                self._merge_llm(draft, llm, prefer_llm_category=unsure)
        elif unsure or missing:
            draft.needs_review = True
            draft.review_reason = "low_confidence" if unsure else f"missing:{','.join(missing)}"
        if fields.review_notes:
            # Conflicting evidence (e.g. OCR'd number ≠ filename number) is a
            # human call: keep the printed value, surface the conflict.
            draft.needs_review = True
            notes = "; ".join(fields.review_notes)
            draft.review_reason = (
                f"{draft.review_reason}; {notes}" if draft.review_reason else notes
            )

        self._finalize(draft)
        if overrides:
            self._apply_overrides(draft, overrides)
        return draft

    # ----------------------------------------------------------- rules

    def _apply_rules(self, signals: DocumentSignals, cls: Classification) -> RuleFields:
        f = RuleFields(language=detect_language(signals.vietnamese_ratio))
        author_role: str | None = None

        # Category layout rules first: they know where the real number/date
        # live (e.g. above "Căn cứ"), so generic matches cannot pre-empt them.
        category_rules = {
            DocCategory.ADMINISTRATIVE: self._admin_rules,
            DocCategory.CONTRACT: self._contract_rules,
            DocCategory.TECHNICAL: self._technical_rules,
        }.get(cls.category)
        if category_rules:
            category_rules(signals, cls, f)

        for fld, raw_label, value in label_values(signals.header_lines):
            if fld == "doc_number":
                f.set("doc_number", extract_doc_number(f"Số: {value}") or value[:64])
            elif fld == "doc_date":
                f.set("doc_date", parse_date_any(value))
            elif fld == "issuer":
                f.set("issuer", value[:160])
            elif fld == "author_role":
                author_role = value[:80]
            elif fld in {"author", "supervisor", "chair", "secretary"}:
                name, student_id = clean_person(value)
                if name:
                    role = {
                        "author": "Tác giả",
                        "supervisor": raw_label,
                        "chair": "Chủ trì",
                        "secretary": "Thư ký",
                    }[fld]
                    kind = {"secretary": "attendee"}.get(fld, fld)
                    f.people.append(person(name, role, kind))
                if student_id:
                    f.extras.setdefault("student_id", student_id)
            elif fld == "attendees":
                names = [clean_person(p)[0] for p in re.split(r"[,;]", value)]
                f.extras["attendees"] = [n for n in names if n] or [value[:200]]
            elif fld in {
                "student_id",
                "program",
                "institution",
                "faculty",
                "period",
                "location",
                "isbn",
                "version",
                "value",
            }:
                f.extras.setdefault(fld, value[:160])
        if author_role:
            for p in f.people:
                if p["kind"] == "author" and p["role"] == "Tác giả":
                    p["role"] = author_role

        f.set("doc_number", extract_doc_number(_before_body(signals.header_lines)))
        f.set("issuer", extract_issuer(signals.header_lines))
        if cls.category is DocCategory.ACADEMIC:
            self._academic_rules(signals, cls, f)

        f.set("title", self._title(signals, cls))
        # Last resort: first date printed near the top ("HÀ NỘI, 06/2026").
        if f.doc_date is None:
            fallback = parse_date_any(" \n ".join(signals.header_lines[:20]))
            if fallback:
                f.doc_date = fallback
                f.sources["doc_date"] = "rule_fallback"
        return f

    @staticmethod
    def _admin_rules(signals: DocumentSignals, cls: Classification, f: RuleFields) -> None:
        ext = AdministrativeMetadataExtractor
        header, tail = signals.header_text, signals.tail_text or signals.header_text
        f.set(
            "doc_number", ext._extract_document_number(header, signals.filename, cls.subtype or "")
        )
        fn_number = ext._extract_number_from_filename(signals.filename, cls.subtype or "")
        lead = re.compile(r"^\d+")
        text_digits = lead.search(f.doc_number or "")
        fn_digits = lead.search(fn_number or "")
        if text_digits and fn_digits and text_digits.group(0) != fn_digits.group(0):
            f.review_notes.append(f"number_mismatch:text={f.doc_number},filename={fn_number}")
        raw_date = ext._extract_promulgation_date(header, signals.filename)
        if raw_date:
            f.set("doc_date", date.fromisoformat(raw_date))
        subject = ext._extract_subject(header)
        if subject:
            # OCR often merges the next "Căn cứ …; Nghị định …" clause into the line.
            subject = subject.split(";")[0].strip(" ,.")
        f.set("subject", subject)
        doc_type = cls.subtype or ext._extract_document_type(header, signals.filename)
        if subject:
            f.set("title", f"{doc_type} {subject[0].lower()}{subject[1:]}")
        signer, role = ext._extract_signatory(tail)
        if signer and clean_person(signer)[0] is None:
            # The layout rule grabbed a heading after the signature block
            # (e.g. an appendix titled "BÁO CÁO TỔNG HỢP"), not a person.
            signer = None
        if signer:
            f.people.append(person(signer, role or "Người ký", "signer"))
        f.extras.update({"signer": signer, "signer_role": role, "doc_type": doc_type})
        replaces = sorted(
            {re.sub(r"\s+", "", m) for m in _REPLACES.findall(f"{header}\n{signals.tail_text}")}
        )
        if replaces:
            f.extras["replaces"] = replaces

    @staticmethod
    def _contract_rules(signals: DocumentSignals, cls: Classification, f: RuleFields) -> None:
        parties: list[str] = []
        for line in signals.header_lines:
            m = _PARTY_LINE.match(line)
            if m:
                party = m.group(2).strip()[:160]
                parties.append(f"Bên {m.group(1).upper()}: {party}")
                f.people.append(person(party, f"Bên {m.group(1).upper()}", "party"))
        if parties:
            f.extras["parties"] = parties

    @staticmethod
    def _academic_rules(signals: DocumentSignals, cls: Classification, f: RuleFields) -> None:
        start = (cls.title_line_index or 0) + 1
        for line in signals.header_lines[start : start + 8]:
            name, _ = clean_person(line)
            if name and line.isupper():
                f.people.append(person(name, "Tác giả", "author"))
                break
        if f.doc_date:
            f.extras.setdefault("year", f.doc_date.year)

    @staticmethod
    def _technical_rules(signals: DocumentSignals, cls: Classification, f: RuleFields) -> None:
        m = _ISBN.search(signals.header_text)
        if m:
            f.extras["isbn"] = re.sub(r"\s+", "", m.group(1))
        for line in signals.header_lines[:20]:
            mm = re.match(r"(?i)^by\s+(.+)$", line)
            if mm:
                name, _ = clean_person(mm.group(1))
                if name:
                    f.people.append(person(name, "Tác giả", "author"))
                    break

    @staticmethod
    def _title(signals: DocumentSignals, cls: Classification) -> str | None:
        lines = signals.header_lines
        if cls.title_line_index is not None:
            head = lines[cls.title_line_index].lstrip("# ").strip()
            cont = _continuation(lines, cls.title_line_index + 1)
            if cls.category is DocCategory.ACADEMIC and cont:
                return cont  # the thesis topic, not "ĐỒ ÁN TỐT NGHIỆP"
            text = f"{head} {cont}".strip() if cont and len(head.split()) <= 3 else head
            return text[:_TITLE_MAX_CHARS]
        # A thesis's inner headings are never its title (its topic is on the
        # cover); leave it to the cover lines below or the LLM fallback.
        headings = () if cls.category is DocCategory.ACADEMIC else signals.headings
        for heading in headings:
            if len(heading.split()) >= 3 and _title_like(heading):
                return heading[:_TITLE_MAX_CHARS]
        for idx, line in enumerate(lines[:10]):
            is_label_value = idx > 0 and lines[idx - 1].rstrip().endswith((":", "："))
            if len(line) >= 6 and not line.isdigit() and not is_label_value and _title_like(line):
                return (line + " " + _continuation(lines, idx + 1)).strip()[:_TITLE_MAX_CHARS]
        # Better no title (UI falls back to the filename) than a wrong one.
        return None

    # ----------------------------------------------------------- draft

    @staticmethod
    def _to_draft(
        signals: DocumentSignals, cls: Classification, f: RuleFields
    ) -> DocumentProfileDraft:
        sources = dict(f.sources)
        sources["doc_category"] = "rule"
        return DocumentProfileDraft(
            doc_category=cls.category,
            doc_subtype=cls.subtype,
            title=f.title,
            doc_number=f.doc_number,
            issuer=f.issuer,
            subject=f.subject,
            language=f.language,
            doc_date=f.doc_date,
            entities=_dedupe_people(f.people),
            extras=dict(f.extras),
            field_sources=sources,
            category_confidence=cls.confidence,
            input_hash=profile_input_hash(signals),
        )

    @staticmethod
    def _missing_required(draft: DocumentProfileDraft) -> list[str]:
        return [
            name
            for name in required_fields_for(draft.doc_category)
            if getattr(draft, name, None) in (None, "", [])
        ]

    @staticmethod
    def _merge_llm(
        draft: DocumentProfileDraft, data: dict[str, Any], *, prefer_llm_category: bool
    ) -> None:
        raw_cat = str(data.get("doc_category") or "").strip().lower()
        if prefer_llm_category and raw_cat in DocCategory._value2member_map_:
            new_cat = DocCategory(raw_cat)
            if new_cat is not draft.doc_category:
                draft.doc_category = new_cat
                draft.doc_subtype = None
            draft.field_sources["doc_category"] = "llm"
            draft.category_confidence = max(draft.category_confidence, 0.7)

        def fill(name: str, value: Any) -> None:
            if value in (None, "", []) or getattr(draft, name) not in (None, "", []):
                return
            setattr(draft, name, value)
            draft.field_sources[name] = "llm"

        fill("doc_subtype", _str(data.get("doc_subtype"), 128))
        fill("title", _str(data.get("title"), _TITLE_MAX_CHARS))
        fill("doc_number", _str(data.get("doc_number"), 64))
        fill("issuer", _str(data.get("issuer"), 160))
        fill("subject", _str(data.get("subject"), 400))
        raw_date = data.get("doc_date")
        if isinstance(raw_date, str):
            fill("doc_date", parse_date_any(raw_date))
        people = data.get("people")
        if isinstance(people, list):
            extra_people = []
            for item in people[:20]:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                kind = str(item.get("kind") or "author")
                extra_people.append(
                    person(
                        str(item["name"])[:80],
                        str(item.get("role") or kind)[:80],
                        kind if kind in _PERSON_KINDS else "author",
                    )
                )
            draft.entities = _dedupe_people(draft.entities + extra_people)
        if isinstance(data.get("extras"), dict):
            for key, value in data["extras"].items():
                draft.extras.setdefault(key, value)

    @staticmethod
    def _finalize(draft: DocumentProfileDraft) -> None:
        draft.extras = validate_extras(draft.doc_category, draft.extras)
        draft.doc_number_norm = normalize_doc_number(draft.doc_number)
        draft.issuer_norm = unaccent_vietnamese(draft.issuer) if draft.issuer else None
        names = sorted({e["name_norm"] for e in draft.entities if e.get("name_norm")})
        draft.entity_names_norm = " | ".join(names) or None
        required = required_fields_for(draft.doc_category)
        if required:
            filled = sum(1 for n in required if getattr(draft, n, None) not in (None, "", []))
            draft.extraction_confidence = round(filled / len(required), 3)
        else:
            draft.extraction_confidence = 1.0 if draft.title else 0.5
        if draft.doc_category is DocCategory.OTHER:
            draft.needs_review = True
            draft.review_reason = draft.review_reason or "uncategorized"

    @staticmethod
    def _apply_overrides(draft: DocumentProfileDraft, overrides: dict[str, Any]) -> None:
        for key, value in overrides.items():
            if key not in DocumentProfileDraft.model_fields or key in {"field_sources"}:
                continue
            if key == "doc_category":
                value = DocCategory(value)
            elif key in {"doc_date", "effective_from", "effective_to"} and isinstance(value, str):
                value = date.fromisoformat(value)
            setattr(draft, key, value)
            draft.field_sources[key] = "manual"
        if overrides:
            draft.needs_review = False
            draft.review_reason = None
            DocumentProfiler._finalize(draft)
            draft.needs_review = False


_BODY_START = re.compile(r"(?i)^\s*-?\s*(?:căn\s+cứ|điều\s+1)")


def _before_body(lines: tuple[str, ...]) -> str:
    """Header lines above the legal basis / first article (where numbers are printed)."""
    out: list[str] = []
    for line in lines:
        if _BODY_START.match(line):
            break
        out.append(line)
    return "\n".join(out)


_BOILERPLATE_HEADING = re.compile(
    r"^(?:loi cam on|loi mo dau|loi noi dau|muc ?luc|tom ?tat|danh muc|phu luc|"
    r"tai lieu tham khao|table of contents|contents|preface|abstract|chuong \d|chapter \d|"
    r"cong hoa xa hoi|doc lap"
    # Numbered section headings ("1.1 Đặt vấn đề", "2. Giải pháp") are body
    # sections, never the document title.
    r"|\d+(?:\.\d+)*\.?\s)"
)


def _title_like(text: str) -> bool:
    """Not a person's name, an e-mail, a label or a front-matter heading."""
    if "@" in text or ":" in text:
        return False
    name, _ = clean_person(text)
    if name and len(text.split()) <= 5:
        return False
    return not _BOILERPLATE_HEADING.match(unaccent_vietnamese(text))


def _continuation(lines: tuple[str, ...], start: int, max_lines: int = 3) -> str:
    """Title lines that wrap onto the next lines (stop at labels / names / e-mails)."""
    parts: list[str] = []
    for line in lines[start : start + max_lines]:
        if ":" in line or "@" in line or len(line) < 3:
            break
        name, _ = clean_person(line)
        if name and len(line.split()) <= 5:
            break
        if sum(len(p) for p in parts) + len(line) > _TITLE_MAX_CHARS:
            break
        parts.append(line)
    return " ".join(parts)


def _dedupe_people(people: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for p in people:
        key = (p.get("name_norm", ""), p.get("kind", ""))
        if key in seen or not key[0]:
            continue
        seen.add(key)
        out.append(p)
    return out


def _str(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:limit]
