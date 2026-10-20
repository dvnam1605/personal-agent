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
    "YÊU CẦU VĂN PHONG:\n"
    "5. Văn phong theo yêu cầu của mục; câu văn mạch lạc, có câu dẫn và liên kết giữa các ý, "
    "tổng hợp và nhóm các ý cùng chủ đề thay vì liệt kê rời rạc; gọi đúng tên văn bản "
    "(loại, số hiệu, ngày) khi viện dẫn.\n"
    "6. Không lặp lại tiêu đề mục, không mở đầu bằng câu chào hay giải thích nhiệm vụ.\n"
    "7. Dùng Markdown gọn: đoạn văn, gạch đầu dòng khi liệt kê, **in đậm** số hiệu/mốc quan trọng; "
    "bảng Markdown khi trình bày số liệu nhiều dòng.\n\n"
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
