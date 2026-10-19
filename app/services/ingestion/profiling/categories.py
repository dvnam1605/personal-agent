"""Document category registry shared by ingestion profiling and query parsing.

Every category declares the cheap signals that identify it (title lines,
filename tokens, body markers), the Vietnamese words people use for it in
questions, and the fields a complete profile of that kind should carry.
Patterns match accent-folded lowercase text (``unaccent_vietnamese``) so
OCR'd or unaccented sources classify the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class DocCategory(StrEnum):
    """Top-level document kinds (``document_profiles.doc_category``)."""

    ADMINISTRATIVE = "administrative"
    REPORT = "report"
    CONTRACT = "contract"
    MINUTES = "minutes"
    TECHNICAL = "technical"
    ACADEMIC = "academic"
    SPREADSHEET = "spreadsheet"
    OTHER = "other"


@dataclass(frozen=True)
class SubtypeRule:
    """A title-line pattern naming a concrete subtype (e.g. "Tờ trình")."""

    label: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class CategorySpec:
    category: DocCategory
    # Title lines in the document header (strongest signal).
    subtypes: tuple[SubtypeRule, ...] = ()
    # Folded filename tokens.
    filename_pattern: re.Pattern[str] | None = None
    # Body markers anywhere in header/tail (weaker, additive).
    markers: tuple[re.Pattern[str], ...] = ()
    # Words users say in questions ("các hợp đồng", "biên bản họp").
    query_synonyms: tuple[str, ...] = ()
    # Profile fields a complete extraction of this kind is expected to have.
    required_fields: tuple[str, ...] = field(default_factory=tuple)


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _sub(label: str, pattern: str) -> SubtypeRule:
    # Title lines are short and start with the type name.
    return SubtypeRule(label, re.compile(rf"^(?:#+\s*)?{pattern}\b"))


CATEGORY_SPECS: tuple[CategorySpec, ...] = (
    CategorySpec(
        DocCategory.ADMINISTRATIVE,
        subtypes=(
            _sub("Quyết định", r"quyet dinh"),
            _sub("Chỉ thị", r"chi thi"),
            _sub("Thông báo", r"thong bao"),
            _sub("Công văn", r"cong van"),
            _sub("Quy chế", r"quy che"),
            _sub("Quy định", r"quy dinh"),
            _sub("Nghị định", r"nghi dinh"),
            _sub("Thông tư", r"thong tu"),
            _sub("Nghị quyết", r"nghi quyet"),
            _sub("Giấy mời", r"giay moi"),
        ),
        filename_pattern=_rx(
            r"(?:^|[_\-\s\d])(?:qd|ct|cv|tb|nq|quyetdinh|chithi|congvan)(?:[_\-\s\d.]|$)"
        ),
        markers=(
            _rx(r"cong hoa xa hoi chu nghia viet nam"),
            _rx(r"\bso\s*:\s*\d+"),
            _rx(r"\bcan cu\b"),
            _rx(r"\bnoi nhan\s*:"),
            _rx(r"\bdieu\s+1\b"),
        ),
        query_synonyms=(
            "quyết định",
            "chỉ thị",
            "thông báo",
            "công văn",
            "quy chế",
            "quy định",
            "nghị định",
            "thông tư",
            "nghị quyết",
            "văn bản hành chính",
            "giấy mời",
        ),
        required_fields=("doc_number", "doc_date", "issuer"),
    ),
    CategorySpec(
        DocCategory.REPORT,
        subtypes=(
            _sub("Báo cáo", r"bao cao"),
            _sub("Tờ trình", r"to trinh"),
            _sub("Kế hoạch", r"ke hoach"),
            _sub("Đề án", r"de an"),
            _sub("Đề xuất", r"de xuat"),
            _sub("Phương án", r"phuong an"),
        ),
        filename_pattern=_rx(
            r"bao[_\-\s]?cao|to[_\-\s]?trinh|ke[_\-\s]?hoach|report|proposal|plan\b"
        ),
        markers=(
            _rx(r"nguoi bao cao\s*:"),
            _rx(r"ky bao cao|bao cao tuan|bao cao thang|bao cao quy|bao cao nam"),
            _rx(r"ket qua thuc hien|kho khan|vuong mac|kien nghi|de xuat"),
            _rx(r"ke hoach (?:tuan|thang|quy|nam) toi"),
        ),
        query_synonyms=(
            "báo cáo",
            "tờ trình",
            "kế hoạch",
            "đề án",
            "đề xuất",
            "phương án",
            "báo cáo tuần",
            "báo cáo tháng",
        ),
        required_fields=("doc_date", "issuer"),
    ),
    CategorySpec(
        DocCategory.CONTRACT,
        subtypes=(
            _sub("Hợp đồng", r"hop dong"),
            _sub("Phụ lục hợp đồng", r"phu luc hop dong"),
            _sub("Thỏa thuận", r"thoa thuan"),
            _sub("Biên bản thanh lý", r"bien ban thanh ly"),
        ),
        filename_pattern=_rx(r"hop[_\-\s]?dong|hd[_\-\s]?\d|contract|agreement"),
        markers=(
            _rx(r"\bben a\b"),
            _rx(r"\bben b\b"),
            _rx(r"gia tri hop dong|thoi han hop dong"),
            _rx(r"dieu khoan|nghia vu cua"),
        ),
        query_synonyms=("hợp đồng", "phụ lục hợp đồng", "thỏa thuận", "hợp đồng ký"),
        required_fields=("doc_date", "entities"),
    ),
    CategorySpec(
        DocCategory.MINUTES,
        subtypes=(
            _sub("Biên bản họp", r"bien ban (?:hop|cuoc hop)"),
            _sub("Biên bản", r"bien ban"),
            _sub("Kết luận cuộc họp", r"ket luan"),
        ),
        filename_pattern=_rx(r"bien[_\-\s]?ban|minutes|meeting"),
        markers=(
            _rx(r"thanh phan(?: tham du)?\s*:"),
            _rx(r"chu tri\s*:"),
            _rx(r"thu ky\s*:"),
            _rx(r"dia diem\s*:"),
            _rx(r"cuoc hop (?:ket thuc|bat dau)"),
        ),
        query_synonyms=("biên bản", "biên bản họp", "cuộc họp", "kết luận cuộc họp"),
        required_fields=("doc_date",),
    ),
    CategorySpec(
        DocCategory.ACADEMIC,
        subtypes=(
            _sub("Đồ án tốt nghiệp", r"do an(?: tot nghiep)?"),
            _sub("Luận văn", r"luan van"),
            _sub("Luận án", r"luan an"),
            _sub("Khóa luận", r"khoa luan"),
            _sub("Bài báo", r"bai bao"),
            _sub("Tiểu luận", r"tieu luan"),
        ),
        filename_pattern=_rx(r"do[_\-\s]?an|luan[_\-\s]?van|khoa[_\-\s]?luan|thesis|\d{8}"),
        markers=(
            _rx(r"giang vien huong dan|\bgvhd\b"),
            _rx(r"sinh vien(?: thuc hien)?\s*:|\bmssv\b"),
            _rx(r"chuong trinh dao tao"),
            _rx(r"loi cam on|tai lieu tham khao"),
            _rx(r"dai hoc|truong dai hoc|hoc vien"),
        ),
        query_synonyms=("đồ án", "luận văn", "luận án", "khóa luận", "bài báo", "tiểu luận"),
        required_fields=("entities",),
    ),
    CategorySpec(
        DocCategory.TECHNICAL,
        subtypes=(
            _sub("Hướng dẫn", r"huong dan"),
            _sub("Quy trình", r"quy trinh"),
            _sub("Tài liệu kỹ thuật", r"tai lieu ky thuat"),
            _sub("Đặc tả", r"dac ta"),
            _sub("Sách", r"(?:table of contents|contents)$"),
        ),
        filename_pattern=_rx(
            r"huong[_\-\s]?dan|quy[_\-\s]?trinh|manual|guide|spec|design|handbook"
        ),
        markers=(
            _rx(r"\bisbn\b"),
            _rx(r"table of contents|\bchapter\s+\d"),
            _rx(r"buoc\s+\d|step\s+\d"),
            _rx(r"cai dat|cau hinh|installation|configuration|api\b"),
        ),
        query_synonyms=(
            "hướng dẫn",
            "quy trình",
            "tài liệu kỹ thuật",
            "đặc tả",
            "sách",
            "tài liệu hướng dẫn",
        ),
        required_fields=(),
    ),
    CategorySpec(
        DocCategory.SPREADSHEET,
        filename_pattern=_rx(r"\.(?:xlsx|xls|csv)$"),
        query_synonyms=("bảng tính", "file excel", "excel", "bảng số liệu"),
    ),
)

SPEC_BY_CATEGORY: dict[DocCategory, CategorySpec] = {s.category: s for s in CATEGORY_SPECS}


def required_fields_for(category: DocCategory) -> tuple[str, ...]:
    spec = SPEC_BY_CATEGORY.get(category)
    return spec.required_fields if spec else ()
