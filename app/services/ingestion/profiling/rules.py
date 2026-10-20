"""Generic, rule-based field extraction shared by every document category.

Covers what most Vietnamese (and English) business documents print near the
top: ``Nhãn: giá trị`` lines, dates in several formats, document numbers and
issuing organisations. Administrative-only layout rules (signature block,
"Về việc" subject) are reused from :class:`AdministrativeMetadataExtractor`
rather than duplicated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.services.ingestion.administrative_extractor import AdministrativeMetadataExtractor
from app.services.skills.matching import unaccent_vietnamese

_LABEL_VALUE = re.compile(r"^(?P<label>[^:：]{2,45})[:：]\s*(?P<value>\S.{0,200})$")

# Folded label → canonical field. First match wins; order matters for prefixes.
_LABEL_FIELDS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"^(?:{pat})$"), fld)
    for pat, fld in (
        (r"so(?: hieu| ky hieu)?", "doc_number"),
        (r"ngay(?: gui bao cao| bao cao| lap| ky| ban hanh| gui)?|date|thoi gian", "doc_date"),
        (r"nguoi bao cao|nguoi lap|nguoi viet|nguoi soan|tac gia|author|authors|by", "author"),
        (r"sinh vien(?: thuc hien)?|hoc vien(?: thuc hien)?|nhom sinh vien", "author"),
        (r"chuc vu|chuc danh", "author_role"),
        (r"phong/bo phan|phong|bo phan|don vi|co quan|to chuc|cong ty|phong ban", "issuer"),
        (r"giang vien huong dan|gvhd|nguoi huong dan|can bo huong dan", "supervisor"),
        (r"mssv|ma so sinh vien|ma sinh vien", "student_id"),
        (r"chuong trinh dao tao|nganh|chuyen nganh", "program"),
        (r"truong|truong dai hoc|vien|hoc vien", "institution"),
        (r"khoa|bo mon", "faculty"),
        (r"ky bao cao|thoi ky bao cao|giai doan", "period"),
        (r"chu tri|chu toa", "chair"),
        (r"thu ky", "secretary"),
        (r"thanh phan(?: tham du)?|thanh vien tham du|nguoi tham du|tham du", "attendees"),
        (r"dia diem", "location"),
        (r"isbn", "isbn"),
        (r"phien ban|version", "version"),
        (r"gia tri hop dong|tong gia tri", "value"),
    )
)


@dataclass
class RuleFields:
    """Everything the rules found; the profiler maps it onto the profile."""

    doc_number: str | None = None
    doc_date: date | None = None
    issuer: str | None = None
    title: str | None = None
    subject: str | None = None
    language: str | None = None
    people: list[dict[str, str]] = field(default_factory=list)
    extras: dict[str, object] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    review_notes: list[str] = field(default_factory=list)

    def set(self, name: str, value: object, source: str = "rule") -> None:
        if value in (None, "", [], {}):
            return
        if getattr(self, name, None) in (None, "", []):
            setattr(self, name, value)
            self.sources[name] = source


# ---------------------------------------------------------------- dates

_MONTHS_EN = {
    m: i
    for i, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        start=1,
    )
}


def _safe_date(year: int, month: int, day: int) -> date | None:
    if not (1900 <= year <= 2100):
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date_any(text: str) -> date | None:
    """First plausible date in ``text`` (VN long form, numeric, month/year, English)."""
    folded = unaccent_vietnamese(re.sub(r"(\d)\.+(\d)", r"\1\2", text))
    # OCR leaves stray dots ("ngày 2.3 tháng 4. năm 2026").
    m = re.search(r"ngay\s*(\d{1,2})[.\s]*thang\s*(\d{1,2})[.\s]*nam\s*(\d{4})", folded)
    if m:
        return _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", folded)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b", folded)
    if m:
        return _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r"\b(?:thang\s*)?(\d{1,2})\s*[/\-]\s*(\d{4})\b", folded)
    if m:
        return _safe_date(int(m.group(2)), int(m.group(1)), 1)
    m = re.search(r"\b(" + "|".join(_MONTHS_EN) + r")\s+(?:(\d{1,2}),?\s+)?(\d{4})\b", folded)
    if m:
        return _safe_date(int(m.group(3)), _MONTHS_EN[m.group(1)], int(m.group(2) or 1))
    return None


# --------------------------------------------------------- label values


def label_values(lines: tuple[str, ...]) -> list[tuple[str, str, str]]:
    """``(field, raw_label, value)`` for recognised ``Nhãn: giá trị`` lines."""
    found: list[tuple[str, str, str]] = []
    for line in lines:
        m = _LABEL_VALUE.match(line.strip())
        if not m:
            continue
        label = unaccent_vietnamese(m.group("label")).strip(" -•*")
        value = m.group("value").strip(" .;")
        if not value or set(value) <= {".", " ", "…"}:
            continue
        for pattern, fld in _LABEL_FIELDS:
            if pattern.match(label):
                found.append((fld, m.group("label").strip(), value))
                break
    return found


# --------------------------------------------------------------- people

_HONORIFIC = re.compile(
    r"^(?:(?:pgs|gs|ts|ths|ks|cn|bs|ls|dr|prof|mr|mrs|ms)\.?\s*)+", re.IGNORECASE
)
_PERSON_TOKEN = re.compile(r"^[A-ZĐÀ-Ỹ][a-zđà-ỹA-ZĐÀ-Ỹ]*$")


def clean_person(raw: str) -> tuple[str | None, str | None]:
    """``("Nguyễn Tuấn Linh", "20220033")`` from "Nguyễn Tuấn Linh - 20220033"."""
    value = raw.strip()
    student_id = None
    m = re.search(r"[-–(]\s*(\d{6,10})\s*\)?\s*$", value)
    if m:
        student_id = m.group(1)
        value = value[: m.start()].strip()
    value = re.split(r"\s*[,;(|]\s*|\s+-\s+", value)[0]
    value = _HONORIFIC.sub("", value).strip(" .")
    words = value.split()
    if not 2 <= len(words) <= 6 or not all(_PERSON_TOKEN.match(w) for w in words):
        return None, student_id
    if value.isupper():
        value = " ".join(w.capitalize() for w in words)
    return value, student_id


def person(name: str, role: str, kind: str) -> dict[str, str]:
    return {"name": name, "name_norm": unaccent_vietnamese(name), "role": role, "kind": kind}


# ------------------------------------------------------- numbers / orgs

_DOC_NUMBER = re.compile(
    r"\b(\d{1,5}\s*/\s*(?:\d{4}\s*/\s*)?[A-ZĐ][A-ZĐ0-9]*(?:\s*-\s*[A-ZĐ0-9]+)*)"
)


def extract_doc_number(header_text: str) -> str | None:
    m = re.search(r"(?i)\bSố\s*[:.]?\s*(\d{1,5}(?:\s*[/\-–]\s*[A-Za-zĐđ0-9]+)+)", header_text)
    raw = m.group(1) if m else None
    if raw is None:
        m2 = _DOC_NUMBER.search(header_text)
        raw = m2.group(1) if m2 else None
    if raw is None:
        return None
    return re.sub(r"\s+", "", raw).replace("–", "-")


def normalize_doc_number(number: str | None) -> str | None:
    if not number:
        return None
    return unaccent_vietnamese(re.sub(r"\s+", "", number)).upper()


_ORG_LINE = re.compile(
    r"^(?:ĐẠI HỌC|TRƯỜNG|HỌC VIỆN|VIỆN|BỘ|SỞ|CỤC|TỔNG CỤC|ỦY BAN|UBND|CÔNG TY|TẬP ĐOÀN|"
    r"NGÂN HÀNG|BỆNH VIỆN|ĐÀI|BAN|PHÒNG|TRUNG TÂM)\b.{3,80}$"
)


def extract_issuer(header_lines: tuple[str, ...]) -> str | None:
    """Organisation printed above the title.

    Known government/agency names come from the admin authority patterns;
    otherwise the first all-caps organisation line near the top is used.
    """
    known = AdministrativeMetadataExtractor._extract_authority("\n".join(header_lines[:15]))
    if known:
        return known
    for line in header_lines[:8]:
        clean = line.strip(" #*_-")
        # Vietnamese upper/lower letters interleave in Unicode, so test case directly.
        if clean.isupper() and _ORG_LINE.match(clean):
            return " ".join(w.capitalize() for w in clean.split())
    return None


def detect_language(vietnamese_ratio: float) -> str:
    return "vi" if vietnamese_ratio >= 0.02 else "en"
