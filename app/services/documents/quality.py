"""Writing prompt and deterministic quality gates for generated documents.

A generated document is a finished deliverable, not a Q&A turn: it must
read well, carry no meta-commentary ("không tìm thấy bằng chứng…", document
ids), and every figure in it must come from the evidence. The prompt asks
for that; the gates below enforce it after generation:

* meta sentences about missing data / evidence / document ids are removed;
* a sentence whose numbers (amounts, dates, document numbers, counts) do
  not all occur in the evidence the section was written from is removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.text import unaccent_vietnamese
from app.services.retrieval.injection_boundary import BOUNDARY_INSTRUCTIONS

DOCUMENT_WRITER_SYSTEM_PROMPT = (
    "Bạn là chuyên viên soạn thảo văn bản chuyên nghiệp. Nhiệm vụ: viết MỘT mục của tài liệu "
    "dựa hoàn toàn trên các bằng chứng (E1, E2, …) được cung cấp.\n\n"
    "YÊU CẦU NỘI DUNG:\n"
    "1. Mọi số liệu, số tiền, tỷ lệ, ngày tháng, số hiệu văn bản, tên người, tên đơn vị phải "
    "chép NGUYÊN VĂN từ bằng chứng. Tuyệt đối không tự tính, làm tròn, suy đoán hay bịa số liệu.\n"
    "2. Mỗi câu có thông tin cụ thể phải gắn nhãn bằng chứng, ví dụ [E2] hoặc [E1, E3].\n"
    "3. Chỉ viết những gì có căn cứ. Nếu bằng chứng cho mục này ít, viết ngắn gọn và chắc chắn; "
    "không viết câu nhận xét về việc thiếu dữ liệu.\n"
    "4. TUYỆT ĐỐI KHÔNG viết các câu kiểu: 'không tìm thấy…', 'bằng chứng không cung cấp…', "
    "'tài liệu chưa đề cập…', 'dữ liệu chưa đầy đủ…', 'thông báo về tài liệu…'; không nhắc mã "
    "tài liệu (doc-…), nhãn E# ngoài ngoặc vuông hay cách hệ thống truy xuất.\n\n"
    "YÊU CẦU VĂN PHONG (đây là văn bản do người viết, KHÔNG phải câu trả lời của chatbot):\n"
    "5. Viết như một chuyên viên đã đọc các văn bản rồi tự tổng hợp lại bằng lời của mình, đúng "
    "thể loại và giọng văn được yêu cầu cho mục (xem 'Văn phong' trong đề bài).\n"
    "6. Mặc định là văn xuôi: đoạn văn có câu chủ đề, các câu liên kết với nhau; gộp các văn bản "
    "cùng chủ đề vào một mạch ý. Chỉ dùng gạch đầu dòng hoặc bảng khi thật sự liệt kê nhiều đối "
    "tượng song song hoặc số liệu nhiều dòng.\n"
    "7. Viện dẫn văn bản tự nhiên trong câu ('Ngày 13/4/2026, Tổng Giám đốc ban hành Quyết định "
    "số 1119/QĐ-TNVN tặng Bằng khen…'), không viết 'Theo bằng chứng…', 'Theo tài liệu E2…'.\n"
    "8. Không tạo tiêu đề con, không in đậm, không kẻ ngang; không mở đầu kiểu 'Dưới đây là…', "
    "'Sau đây là…', không kết thúc bằng câu hỏi hay lời mời, không lặp lại tiêu đề mục.\n\n"
    f"{BOUNDARY_INSTRUCTIONS}\n"
)

_META = re.compile(
    r"\b(?:khong tim thay|chua tim thay|khong co (?:du )?(?:du lieu|bang chung|thong tin)|"
    r"bang chung (?:hien (?:tai|co)|khong|chua|cua muc)|chua (?:co|du) (?:du lieu|bang chung|co so)|"
    r"tai lieu (?:nguon )?(?:khong|chua) (?:de cap|cung cap|co)|du lieu (?:thu thap )?chua day du|"
    r"thong bao ve (?:tai lieu|du lieu)|ma dinh danh|ma tai lieu|doi chieu|doi soat|"
    r"luu y ve pham vi du lieu)\b|\bdoc-[0-9a-f]*\b"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[A-ZĐÀ-Ỹ*\[(\"“])")
_NUMBER = re.compile(r"\d+(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
_LIST_MARKER = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)]|[IVX]{1,4}\.)\s+")
_BADGE = re.compile(r"\[\d+(?:\]\[\d+)*\]")


def _number_values(text: str) -> set[int]:
    """Integer values of every number in ``text`` (VN/EN separators, dates split)."""
    values: set[int] = set()
    for token in _NUMBER.findall(text):
        if re.fullmatch(r"\d+(?:[.,]\d{3})+", token):
            values.add(int(re.sub(r"[.,]", "", token)))  # 1.500.000 / 1,500,000
        else:
            for part in re.split(r"[.,]", token):
                if part:
                    values.add(int(part))
    return values


def _significant(values: set[int]) -> set[int]:
    # 0–10 are too common (list numbering, "Điều 2", "09 tập thể") to verify.
    return {v for v in values if v > 10}


@dataclass
class GateReport:
    removed_meta: list[str] = field(default_factory=list)
    removed_unverified: list[str] = field(default_factory=list)


def _sentences(line: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(line) if s.strip()]


def apply_quality_gates(text: str, evidence_text: str) -> tuple[str, GateReport]:
    """Drop meta-commentary and sentences with numbers absent from the evidence."""
    report = GateReport()
    evidence_values = _number_values(evidence_text)
    out_lines: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            out_lines.append(line)
            continue
        if line.strip() in {"---", "***"}:
            continue
        marker = _LIST_MARKER.match(line)
        prefix = marker.group(0) if marker else ""
        body = line[len(prefix) :]
        kept: list[str] = []
        for sentence in _sentences(body):
            folded = unaccent_vietnamese(sentence)
            if _META.search(folded):
                report.removed_meta.append(sentence.strip()[:200])
                continue
            claimed = _significant(_number_values(_BADGE.sub("", sentence)))
            if claimed - evidence_values:
                report.removed_unverified.append(sentence.strip()[:200])
                continue
            kept.append(sentence)
        if kept:
            out_lines.append(prefix + " ".join(kept))
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    # A heading left without any content under it is dropped too.
    cleaned = re.sub(r"(?m)^#{1,6} .+\n+(?=#{1,6} |\Z)", "", cleaned).strip()
    return cleaned, report


_CHATBOT_SENTENCE = re.compile(
    r"^(?:duoi day la|sau day la|duoi day|sau day|hy vong|neu ban can|neu anh/chi can|"
    r"ban co the|xin luu y rang toi|toi da|toi se)\b"
)


def polish_style(text: str) -> str:
    """Remove chatbot formatting habits from a written section.

    Headings inside a section, bold/italic markers, horizontal rules and
    opener/closer sentences ("Dưới đây là…", "Hy vọng…") have no place in a
    document a person wrote; the content around them is kept.
    """
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.fullmatch(r"[-*_]{3,}", stripped):
            continue
        if stripped.startswith("#"):
            # Inner sub-heading: keep its words only if they carry content.
            heading = stripped.lstrip("#").strip().strip("*_ ")
            if heading and not re.fullmatch(r"[\dIVX.]+\s*\S{0,40}", heading):
                lines.append("")
            continue
        line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), line)
        line = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", line)
        marker = _LIST_MARKER.match(line)
        prefix = marker.group(0) if marker else ""
        body = line[len(prefix) :]
        kept = [
            s
            for s in _sentences(body)
            if not _CHATBOT_SENTENCE.match(unaccent_vietnamese(s.strip()))
        ]
        if kept or not body.strip():
            lines.append(prefix + " ".join(kept) if kept else line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
