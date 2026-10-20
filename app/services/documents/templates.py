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
    # The slot the outline planner expands into sections named after the
    # actual topics of the source documents (one per programme, event or
    # field of work) instead of a generic "Nội dung …" heading.
    topical: bool = False


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


def _s(heading: str, purpose: str, *, topical: bool = False) -> SectionSpec:
    return SectionSpec(heading, purpose, topical)


TEMPLATES: tuple[DocTemplate, ...] = (
    DocTemplate(
        key="weekly_report",
        label="Báo cáo tuần",
        category="report",
        triggers=("bao cao tuan",),
        sections=(
            _s(
                "I. Kết quả công việc trong tuần",
                "Các đầu việc đã thực hiện, kết quả, tiến độ",
                topical=True,
            ),
            _s("II. Khó khăn, vướng mắc", "Vấn đề phát sinh và nguyên nhân"),
            _s("III. Kế hoạch tuần tới", "Việc dự kiến, mốc thời gian"),
            _s("IV. Đề xuất, kiến nghị", "Đề xuất cần cấp trên hỗ trợ/quyết định"),
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
            _s("II. Nội dung đề xuất", "Nội dung cụ thể cần phê duyệt", topical=True),
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
            _s(
                "II. Diễn biến cuộc họp",
                "Các vấn đề được trình bày và thảo luận",
                topical=True,
            ),
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
            _s(
                "II. Nhiệm vụ cụ thể",
                "Các nhiệm vụ, thời gian, đơn vị thực hiện",
                topical=True,
            ),
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
            _s("2. Nội dung chính", "Các ý chính theo cấu trúc tài liệu", topical=True),
            _s("3. Điểm cần lưu ý", "Số liệu, thời hạn, trách nhiệm, điểm mới hoặc thay đổi"),
        ),
    ),
    DocTemplate(
        key="summary_report",
        label="Báo cáo tổng hợp",
        category="report",
        triggers=("bao cao", "tong hop", "tai lieu", "van ban"),
        sections=(
            _s(
                "I. Đặc điểm, tình hình chung",
                "Bối cảnh, phạm vi và các văn bản làm căn cứ của báo cáo",
            ),
            _s(
                "II. Kết quả thực hiện",
                "Kết quả cụ thể theo từng lĩnh vực, nhóm công việc trong tài liệu",
                topical=True,
            ),
            _s("III. Đánh giá chung", "Ưu điểm, hạn chế và nguyên nhân rút ra từ tài liệu"),
            _s("IV. Phương hướng, kiến nghị", "Nhiệm vụ tiếp theo và kiến nghị"),
        ),
    ),
)

_DEFAULT_KEY = "summary_report"
TEMPLATE_BY_KEY: dict[str, DocTemplate] = {t.key: t for t in TEMPLATES}

# How a human specialist writes each kind: register, sentence shape, what
# a paragraph does. Prose by default — lists only for genuinely parallel items.
_PROSE = (
    "Viết thành các đoạn văn xuôi liền mạch như người đã đọc kỹ tài liệu rồi tự tổng hợp lại: "
    "mỗi đoạn có câu chủ đề, các câu nối với nhau bằng từ liên kết, tổng hợp nhiều văn bản vào "
    "cùng một mạch ý thay vì liệt kê từng văn bản. "
)
STYLE_GUIDES: dict[str, str] = {
    "weekly_report": _PROSE
    + "Giọng báo cáo công việc ngắn gọn, ở ngôi tổ chức/đơn vị ('Trong tuần, bộ phận đã…'). "
    "Mỗi mục 1–2 đoạn; chỉ dùng gạch đầu dòng cho danh sách đầu việc khi có từ bốn việc trở lên.",
    "proposal": _PROSE
    + "Giọng tờ trình hành chính: lập luận chặt chẽ đi từ căn cứ pháp lý, thực tiễn đến đề xuất; "
    "dùng các cụm 'Căn cứ…', 'Xét thấy…', 'Để…, [đơn vị] kính trình…'. Mục Kiến nghị kết thúc "
    "bằng câu kính trình xem xét, phê duyệt.",
    "meeting_minutes": "Giọng biên bản: ghi nhận khách quan ở thì quá khứ, câu ngắn, chính xác "
    "('Cuộc họp đã thống nhất…', 'Đồng chí … trình bày…'). Thành phần và phân công nhiệm vụ có "
    "thể trình bày dạng danh sách; nội dung thảo luận viết thành đoạn văn.",
    "plan": _PROSE
    + "Giọng kế hoạch: rõ mục tiêu, nhiệm vụ, đơn vị chủ trì, thời hạn. Phần tiến độ có thể dùng "
    "bảng (nhiệm vụ – đơn vị – thời gian); các phần khác viết đoạn văn.",
    "official_letter": "Giọng công văn: trang trọng, ngắn gọn, đi thẳng vào việc; viện dẫn văn "
    "bản liên quan ở câu đầu; phần đề nghị nêu rõ việc cần làm và thời hạn. Viết đoạn văn, "
    "không dùng gạch đầu dòng trừ khi liệt kê các đầu việc yêu cầu.",
    "document_summary": _PROSE
    + "Giọng tóm lược trung thực: giữ đúng ý, đúng số liệu của tài liệu gốc, diễn đạt lại gọn hơn "
    "chứ không chép nguyên câu; nêu tài liệu theo đúng tên gọi, số hiệu, ngày.",
    "summary_report": _PROSE
    + "Giọng báo cáo tổng hợp hành chính: mở mục bằng một câu khái quát, sau đó đi vào các nội dung "
    "theo nhóm chủ đề, có nhận định ngắn rút ra từ chính các văn bản. Chỉ dùng danh sách khi "
    "liệt kê nhiều đối tượng song song (ví dụ danh sách tập thể được khen thưởng).",
}


def style_for(template: DocTemplate) -> str:
    return STYLE_GUIDES.get(template.key, _PROSE)


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
