"""High-precision heuristic and regex metadata extractor for Vietnamese administrative documents.

Follows official Vietnamese administrative drafting regulations (Nghị định 30/2020/NĐ-CP)
to extract document number (Số hiệu), promulgation date (Ngày ban hành), document type,
issuing authority, subject, and signatory (Người ký & Chức vụ).
"""

from __future__ import annotations

import re
from typing import ClassVar

from app.domain.models.ingestion.administrative_metadata import AdministrativeMetadata


class AdministrativeMetadataExtractor:
    """Extracts structured administrative attributes from text and file provenance."""

    KNOWN_LEADERSHIP: ClassVar[list[tuple[str, str]]] = [
        ("Đỗ Tiến Sỹ", "Tổng Giám đốc"),
        ("Vũ Hải Quang", "Phó Tổng Giám đốc"),
        ("Ngô Minh Hiển", "Phó Tổng Giám đốc"),
        ("Phạm Mạnh Hùng", "Phó Tổng Giám đốc"),
        ("Trần Minh Hùng", "Phó Tổng Giám đốc"),
    ]

    AUTHORITY_PATTERNS: ClassVar[list[tuple[re.Pattern[str], str]]] = [
        (re.compile(r"(?i)ĐÀI\s+TIẾNG\s+NÓI\s+VIỆT\s+NAM"), "Đài Tiếng nói Việt Nam"),
        (re.compile(r"(?i)CHÍNH\s+PHỦ"), "Chính phủ"),
        (re.compile(r"(?i)BỘ\s+THÔNG\s+TIN\s+VÀ\s+TRUYỀN\s+THÔNG"), "Bộ Thông tin và Truyền thông"),
        (re.compile(r"(?i)BỘ\s+NỘI\s+VỤ"), "Bộ Nội vụ"),
        (re.compile(r"(?i)BỘ\s+TÀI\s+CHÍNH"), "Bộ Tài chính"),
    ]

    @classmethod
    def extract(cls, raw_text: str, filename: str = "") -> AdministrativeMetadata:
        """Extract metadata from raw document text with optional filename fallback."""
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        header_text = "\n".join(lines[:35]) if lines else ""
        tail_text = "\n".join(lines[-35:]) if lines else ""

        # 1. Document Type (Thể loại văn bản)
        doc_type = cls._extract_document_type(header_text, filename)

        # 2. Document Number (Số ký hiệu văn bản)
        doc_number = cls._extract_document_number(header_text, filename, doc_type)

        # 3. Promulgation Date (Ngày ban hành)
        promulgation_date = cls._extract_promulgation_date(header_text, filename)

        # 4. Issuing Authority (Cơ quan ban hành)
        authority = cls._extract_authority(header_text)

        # 5. Subject (Trích yếu nội dung)
        subject = cls._extract_subject(header_text)

        # 6. Signer & Role (Người ký & Chức vụ)
        signer_name, signer_role = cls._extract_signatory(tail_text)

        confidence = 1.0
        if not doc_number:
            confidence -= 0.3
        if not promulgation_date:
            confidence -= 0.2
        if not signer_name:
            confidence -= 0.2
        confidence = max(0.1, round(confidence, 2))

        return AdministrativeMetadata(
            document_number=doc_number,
            document_type=doc_type,
            issuing_authority=authority,
            promulgation_date=promulgation_date,
            signer_name=signer_name,
            signer_role=signer_role,
            subject=subject,
            confidence=confidence,
            extra_fields={"filename": filename} if filename else {},
        )

    @classmethod
    def _extract_document_type(cls, header_text: str, filename: str) -> str:
        fn_upper = filename.upper()
        if "_CT" in fn_upper or "CT_" in fn_upper or "CHITHI" in fn_upper:
            return "Chỉ thị"
        if "_QD" in fn_upper or "QD_" in fn_upper or "QUYETDINH" in fn_upper:
            return "Quyết định"

        # Check main heading line before citations
        for line in header_text.splitlines():
            clean = line.strip().lstrip("#").strip()
            if re.match(r"(?i)^CHỈ\s+THỊ\b", clean):
                return "Chỉ thị"
            if re.match(r"(?i)^QUYẾT\s+ĐỊNH\b", clean):
                return "Quyết định"
            if re.match(r"(?i)^QUY\s+CHẾ\b", clean):
                return "Quy chế"
            if re.match(r"(?i)^THÔNG\s+BÁO\b", clean):
                return "Thông báo"
            if re.match(r"(?i)^-?\s*Căn\s+cứ\b", clean):
                break

        return "Quyết định"

    @classmethod
    def _extract_document_number(cls, header_text: str, filename: str, doc_type: str) -> str | None:
        fn_num = cls._extract_number_from_filename(filename, doc_type)

        # Restrict search to lines BEFORE the first "QUYẾT ĐỊNH" / "CHỈ THỊ" or Căn cứ block
        pre_heading_lines: list[str] = []
        for line in header_text.splitlines():
            clean = line.strip()
            if re.match(r"(?i)^#*\s*(?:QUYẾT\s+ĐỊNH|CHỈ\s+THỊ|QUY\s+CHẾ)\b", clean):
                break
            if re.match(r"(?i)^-?\s*Căn\s+cứ\b", clean):
                break
            pre_heading_lines.append(clean)
        pre_heading_text = "\n".join(pre_heading_lines)

        # Pattern 1: Standard header "Số: 427 /QĐ-TNVN" or "Số 109 -QĐ/TNVN" before citations
        match = re.search(
            r"(?i)Số\s*:\s*([0-9]+(?:\s*[\-–/]\s*[A-ZĐa-zđ0-9/]+)+)",
            pre_heading_text,
        )
        if not match:
            match = re.search(
                r"(?i)Số\s+([0-9]+\s*[\-–/]\s*[A-ZĐa-zđ0-9/]+)",
                pre_heading_text,
            )

        if match:
            raw_num = match.group(1).strip()
            cleaned = re.sub(r"\s+", "", raw_num).replace("–", "-")
            # Harmonize with filename if OCR dropped a digit e.g. "42" vs "427"
            if fn_num and fn_num != cleaned:
                m_fn = re.search(r"^\d+", fn_num)
                m_cl = re.search(r"^\d+", cleaned)
                if m_fn and m_cl:
                    fn_d, cl_d = m_fn.group(0), m_cl.group(0)
                    if fn_d.startswith(cl_d) and len(fn_d) > len(cl_d):
                        return fn_num
            return cleaned

        # Fallback to filename if header had no document number (or only had citations)
        if fn_num:
            return fn_num

        return None

    @classmethod
    def _extract_number_from_filename(cls, filename: str, doc_type: str) -> str | None:
        if not filename:
            return None
        # Match pattern like _427QD_ or _80QD_ or _CT1838_
        m_qd = re.search(r"_(\d+)QD(?:_|\.|$)", filename, re.IGNORECASE)
        if m_qd:
            return f"{m_qd.group(1)}/QĐ-TNVN"
        m_ct = re.search(r"_(?:CT)?(\d+)(?:CT)?(?:_|\.|$)", filename, re.IGNORECASE)
        if m_ct and (doc_type == "Chỉ thị" or "CT" in filename.upper()):
            return f"{m_ct.group(1)}/CT-TNVN"
        return None

    @classmethod
    def _extract_promulgation_date(cls, header_text: str, filename: str) -> str | None:
        # Check pre-citations lines first for the promulgation location and date
        pre_heading_lines: list[str] = []
        for line in header_text.splitlines():
            clean = line.strip()
            if re.match(r"(?i)^-?\s*Căn\s+cứ\b", clean):
                break
            pre_heading_lines.append(clean)
        pre_text = "\n".join(pre_heading_lines)

        # Standard: "Hà Nội, ngày 25 tháng 02 năm 2026" or "ngày 28 tháng 4 năm 2026"
        # Handles OCR artifacts like "ngày 2.3 tháng 4. năm 2026"
        normalized_pre = re.sub(r"(\d)\.+(\d)", r"\1\2", pre_text)
        date_match = re.search(
            r"(?i)(?:Hà Nội|[A-ZĐÀ-Ỹa-zđà-ỹ\s]+),\s*ngày\s*(\d{1,2})\s*tháng\s*(\d{1,2})\s*năm\s*(\d{4})",
            normalized_pre,
        )
        if not date_match:
            date_match = re.search(
                r"(?i)ngày\s*(\d{1,2})\s*tháng\s*(\d{1,2})\s*năm\s*(\d{4})",
                normalized_pre,
            )
        if date_match:
            day, month, year = date_match.groups()[-3:]
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

        # Fallback to filename: e.g. "18-3-2026-954776_427QD_25_02_2026.md" -> 25_02_2026
        m_fn_date = re.search(r"_(\d{1,2})_(\d{1,2})_(\d{4})\.md$", filename, re.IGNORECASE)
        if m_fn_date:
            day, month, year = m_fn_date.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

        # Prefix date fallback: "18-3-2026-..."
        m_prefix_date = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})", filename)
        if m_prefix_date:
            day, month, year = m_prefix_date.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

        return None

    @classmethod
    def _extract_authority(cls, header_text: str) -> str:
        for pattern, name in cls.AUTHORITY_PATTERNS:
            if pattern.search(header_text):
                return name
        return "Đài Tiếng nói Việt Nam"

    @classmethod
    def _extract_subject(cls, header_text: str) -> str | None:
        subj_match = re.search(r"(?i)Về\s+(?:việc\s+)?([^\n\r]+)", header_text)
        if subj_match:
            clean = subj_match.group(1).strip()
            clean = re.sub(r"^[#\s\*_]+|[#\s\*_]+$", "", clean)
            if not clean.lower().startswith("việc"):
                return f"Về việc {clean}"
            return f"Về {clean}"
        return None

    @classmethod
    def _extract_signatory(cls, tail_text: str) -> tuple[str | None, str | None]:
        for name, role in cls.KNOWN_LEADERSHIP:
            if name in tail_text:
                return name, role

        # Generic detection of leadership signature blocks
        if "TỔNG GIÁM ĐỐC" in tail_text:
            if "PHÓ TỔNG GIÁM ĐỐC" in tail_text or "KT. TỔNG GIÁM ĐỐC" in tail_text:
                return None, "Phó Tổng Giám đốc"
            return None, "Tổng Giám đốc"

        return None, None
