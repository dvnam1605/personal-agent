"""Templates for documents the assistant can write on request.

Each template fixes the section skeleton and tone for one document kind so
the LLM only has to fill sections from evidence (and can be skipped
entirely: the template outline is a usable default when the gateway is
down). Administrative kinds carry the national motto header (Nghị định
30/2020/NĐ-CP layout) in the .docx output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.text import unaccent_vietnamese


@dataclass(frozen=True)
class SectionSpec:
    heading: str
    purpose: str


@dataclass(frozen=True)
class DocTemplate:
    key: str
    label: str
    category: str  # DocCategory value of the produced document
    triggers: tuple[str, ...]  # folded phrases that select this template
    sections: tuple[SectionSpec, ...]
    tone: str = "văn phong hành chính - công vụ, khách quan, súc tích"
    formal_header: bool = False
    extra: dict[str, str] = field(default_factory=dict)


def _s(heading: str, purpose: str) -> SectionSpec:
    return SectionSpec(heading, purpose)


TEMPLATES: tuple[DocTemplate, ...] = (
    DocTemplate(
        key="weekly_report",
        label="Báo cáo tuần",
        category="report",
        triggers=("bao cao tuan",),
        sections=(
            _s("I. Tổng quan công việc trong tuần", "Các đầu việc đã thực hiện, kết quả, tiến độ"),
            _s("II. Khó khăn – vướng mắc", "Vấn đề phát sinh và nguyên nhân"),
            _s("III. Kế hoạch tuần tới", "Việc dự kiến, mốc thời gian"),
            _s("IV. Đề xuất – kiến nghị", "Đề xuất cần cấp trên hỗ trợ/quyết định"),
        ),
    ),
    DocTemplate(
        key="proposal",
        label="Tờ trình",
        category="report",
        triggers=("to trinh",),
        formal_header=True,
        sections=(
            _s("I. Sự cần thiết", "Bối cảnh, căn cứ và lý do đề xuất"),
            _s("II. Nội dung đề xuất", "Nội dung cụ thể cần phê duyệt"),
            _s("III. Kinh phí và nguồn lực", "Dự toán, nhân sự, thời gian (nếu có trong tài liệu)"),
            _s("IV. Kiến nghị", "Nội dung kính trình cấp có thẩm quyền xem xét, phê duyệt"),
        ),
    ),
    DocTemplate(
        key="meeting_minutes",
        label="Biên bản họp",
        category="minutes",
        triggers=("bien ban hop", "bien ban cuoc hop", "bien ban"),
        formal_header=True,
        sections=(
            _s(
                "I. Thời gian, địa điểm, thành phần",
                "Thời gian, địa điểm, chủ trì, thư ký, người dự",
            ),
            _s("II. Nội dung cuộc họp", "Các vấn đề được trình bày và thảo luận"),
            _s("III. Kết luận và phân công", "Kết luận, nhiệm vụ, người phụ trách, thời hạn"),
        ),
    ),
    DocTemplate(
        key="plan",
        label="Kế hoạch",
        category="report",
        triggers=("ke hoach",),
        formal_header=True,
        sections=(
            _s("I. Mục đích, yêu cầu", "Mục tiêu và yêu cầu cần đạt"),
            _s("II. Nội dung và tiến độ", "Các nhiệm vụ, thời gian, đơn vị thực hiện"),
            _s("III. Tổ chức thực hiện", "Phân công trách nhiệm, kinh phí, chế độ báo cáo"),
        ),
    ),
    DocTemplate(
        key="official_letter",
        label="Công văn",
        category="administrative",
        triggers=("cong van",),
        formal_header=True,
        sections=(
            _s("Nội dung", "Nội dung chính của công văn, viện dẫn văn bản liên quan"),
            _s("Đề nghị", "Đề nghị/yêu cầu đối với nơi nhận và thời hạn"),
        ),
    ),
    DocTemplate(
        key="document_summary",
        label="Tóm tắt tài liệu",
        category="report",
        triggers=("tom tat", "tom luoc", "tong hop noi dung"),
        tone="trình bày rõ ràng, trung thực với tài liệu gốc",
        sections=(
            _s("1. Thông tin chung", "Loại, số hiệu/tên, ngày, cơ quan/tác giả của tài liệu"),
            _s("2. Nội dung chính", "Các ý chính theo cấu trúc tài liệu"),
            _s("3. Điểm cần lưu ý", "Số liệu, thời hạn, trách nhiệm, điểm mới hoặc thay đổi"),
        ),
    ),
    DocTemplate(
        key="summary_report",
        label="Báo cáo tổng hợp",
        category="report",
        triggers=("bao cao", "tong hop", "tai lieu", "van ban"),
        sections=(
            _s("I. Mở đầu", "Mục đích, phạm vi và nguồn tài liệu sử dụng"),
            _s("II. Nội dung tổng hợp", "Các nội dung chính rút ra từ tài liệu, nhóm theo chủ đề"),
            _s("III. Đánh giá", "Nhận xét, kết quả, tồn tại dựa trên tài liệu"),
            _s("IV. Kết luận và kiến nghị", "Kết luận và đề xuất tiếp theo"),
        ),
    ),
)

_DEFAULT_KEY = "summary_report"
TEMPLATE_BY_KEY: dict[str, DocTemplate] = {t.key: t for t in TEMPLATES}

_GENERATION_VERB = re.compile(
    r"\b(?:viet|soan|soan thao|tao|lap|xay dung|chuan bi|tong hop thanh|lam)\b"
)
_GENERATION_NOUN = re.compile(
    r"\b(?:bao cao|to trinh|bien ban|ke hoach|cong van|tom tat|tai lieu|van ban|de cuong|"
    r"thong bao)\b"
)
_NOT_DOCUMENT = re.compile(r"\b(?:email|mail|thu dien tu|lich|cuoc hen|su kien)\b")


def is_generation_request(text: str) -> bool:
    """'viết/soạn/lập … báo cáo/tờ trình/biên bản …' — not e-mail or calendar."""
    folded = unaccent_vietnamese(text)
    verb = _GENERATION_VERB.search(folded)
    if not verb or _NOT_DOCUMENT.search(folded):
        return False
    noun = _GENERATION_NOUN.search(folded, verb.end())
    return noun is not None and noun.start() - verb.end() <= 25


def select_template(text: str) -> DocTemplate:
    folded = unaccent_vietnamese(text)
    for template in TEMPLATES:
        if any(re.search(rf"\b{re.escape(t)}\b", folded) for t in template.triggers):
            return template
    return TEMPLATE_BY_KEY[_DEFAULT_KEY]
