"""High-precision heuristic and LLM-assisted metadata extractor for Vietnamese administrative documents.

Follows official Vietnamese administrative drafting regulations (Nghị định 30/2020/NĐ-CP)
to extract document number (Số hiệu), promulgation date (Ngày ban hành), document type,
issuing authority, subject, and signatory (Người ký & Chức vụ) with zero hardcoded individuals.
"""

from __future__ import annotations

import json
import logging
import re
from typing import ClassVar

import httpx

from app.core.config import settings
from app.domain.models.ingestion.administrative_metadata import AdministrativeMetadata

logger = logging.getLogger(__name__)

# The subject line sits above the legal basis; the basis and the articles
# cite other documents "về việc …", which must never become the subject.
_SUBJECT_STOP = re.compile(
    r"(?i)^(?:căn\s+cứ|theo\s+đề\s+nghị|xét\s+(?:đề\s+nghị|tình\s+hình)|điều\s*1\b|"
    r"quyết\s+định\s*:|nơi\s+nhận)"
)
# "Về việc …", "V/v …", or "<TYPE NAME> Về việc …" on one line; the prefix
# must be upper case (checked in code: OCR misspells type names, "CHÍ THỊ").
_SUBJECT_LINE = re.compile(r"^(?P<prefix>.*?)(?<!\w)(?:V/v|Về|VỀ)[\s.:]+(?P<rest>.+)$")
_ARTICLE_ONE = re.compile(r"(?m)^[\s#*_]*Điều\s*1\s*[.:]\s*(.+)$")
# Where the operative clause of Điều 1 ends: recipients ("cho …"), lists
# ("gồm …", "như sau", "với nội dung"), the person's position (", chuyên
# viên …"), or the sentence.
_ARTICLE_ONE_CUT = re.compile(
    r"(?i)\s+cho\s+(?=[^\s,]+\s)|[;:]|\.(?:\s|$)|"
    r"\s+(?:gồm|bao gồm|như sau|cụ thể|với (?:các )?nội dung|theo danh sách)\b|"
    r",\s*(?=(?:chức vụ|chuyên viên|phó|trưởng|giám đốc|phóng viên|biên tập viên|kỹ thuật viên|"
    r"nhân viên|kể từ|đã có|thuộc|hiện là|sinh ngày|đến công tác|về công tác)\b)"
)
_ARTICLE_ONE_ACTION = re.compile(
    r"(?i)^(?:(?:ông|bà|đồng chí)\s+[^,]{2,40}?\s+)?(?:tặng|công nhận|ban hành|phê duyệt|"
    r"thành lập|cử|điều động|bổ nhiệm|tuyển dụng|giao|chấm dứt|cho thôi|thôi giữ|thôi việc|"
    r"kỷ luật|khen thưởng|điều chỉnh|sửa đổi|bổ sung|bãi bỏ|quy định|phân công|tiếp nhận|"
    r"nâng|xếp|chuyển|kiện toàn|hợp nhất|giải thể|đổi tên|cho phép|chỉ định|ủy quyền|"
    r"tổ chức|triển khai|áp dụng|công bố|thu hồi|hủy bỏ|đình chỉ|gia hạn|cấp|duyệt|chi|"
    r"trích|hỗ trợ|tạm ứng|thanh lý|mua sắm|điều chuyển|luân chuyển|biệt phái|nghỉ hưu|"
    r"phê chuẩn|chuẩn y|xác nhận|thực hiện)\b"
)


class AdministrativeMetadataExtractor:
    """Extracts structured administrative attributes using layout algorithms and LLM assistance."""

    AUTHORITY_PREFIX_PATTERNS: ClassVar[list[tuple[re.Pattern[str], str | None]]] = [
        (re.compile(r"(?i)\bĐÀI\s+TIẾNG\s+NÓI\s+VIỆT\s+NAM\b"), "Đài Tiếng nói Việt Nam"),
        (re.compile(r"(?i)\bĐÀI\s+TRUYỀN\s+HÌNH\s+VIỆT\s+NAM\b"), "Đài Truyền hình Việt Nam"),
        (re.compile(r"(?i)\bCHÍNH\s+PHỦ\b"), "Chính phủ"),
        (re.compile(r"(?i)\bTHỦ\s+TƯỚNG\s+CHÍNH\s+PHỦ\b"), "Thủ tướng Chính phủ"),
        (re.compile(r"(?i)\bBỘ\s+THÔNG\s+TIN\s+VÀ\s+TRUYỀN\s+THÔNG\b"), "Bộ Thông tin và Truyền thông"),
        (re.compile(r"(?i)\bBỘ\s+NỘI\s+VỤ\b"), "Bộ Nội vụ"),
        (re.compile(r"(?i)\bBỘ\s+TÀI\s+CHÍNH\b"), "Bộ Tài chính"),
        (re.compile(r"(?i)\bBỘ\s+GIÁO\s+DỤC\s+VÀ\s+ĐÀO\s+TẠO\b"), "Bộ Giáo dục và Đào tạo"),
        (re.compile(r"(?i)\bBỘ\s+Y\s+TẾ\b"), "Bộ Y tế"),
        (re.compile(r"(?i)\bBỘ\s+CÔNG\s+THƯƠNG\b"), "Bộ Công Thương"),
        (
            re.compile(r"(?i)\b(?:BỘ|TỔNG\s+CỤC|CỤC|ỦY\s+BAN\s+NHÂN\s+DÂN|UBND|HỘI\s+ĐỒNG\s+NHÂN\s+DÂN|HĐND|HỌC\s+VIỆN|TRƯỜNG\s+ĐẠI\s+HỌC|VIỆN|TẬP\s+ĐOÀN|TỔNG\s+CÔNG\s+TY|CÔNG\s+TY|SỞ|BAN)\s+[A-ZĐÀ-Ỹa-zđà-ỹ\s0-9–\-]{3,60}\b"),
            None,
        ),
    ]

    ROLE_SIGNATURE_PATTERNS: ClassVar[list[tuple[re.Pattern[str], str]]] = [
        # Explicit Deputy / KT patterns (Nghị định 30/2020/NĐ-CP: KT. = Ký thay người đứng đầu)
        (re.compile(r"(?i)\b(?:KT[\./]|K/T|KÝ\s+THAY)\s*PH[ÓỐ]\s+TỔNG\s+GIÁM\s+ĐỐC\b"), "Phó Tổng Giám đốc"),
        (re.compile(r"(?i)\b(?:KT[\./]|K/T|KÝ\s+THAY)\s*TỔNG\s+GIÁM\s+ĐỐC\b"), "Phó Tổng Giám đốc"),
        (re.compile(r"(?i)\b(?:KT[\./]|K/T|KÝ\s+THAY)\s*PH[ÓỐ]\s+GIÁM\s+ĐỐC\b"), "Phó Giám đốc"),
        (re.compile(r"(?i)\b(?:KT[\./]|K/T|KÝ\s+THAY)\s*GIÁM\s+ĐỐC\b"), "Phó Giám đốc"),
        (re.compile(r"(?i)\b(?:KT[\./]|K/T|KÝ\s+THAY)\s*PH[ÓỐ]\s+CHỦ\s+TỊCH\b"), "Phó Chủ tịch"),
        (re.compile(r"(?i)\b(?:KT[\./]|K/T|KÝ\s+THAY)\s*CHỦ\s+TỊCH\b"), "Phó Chủ tịch"),
        (re.compile(r"(?i)\b(?:KT[\./]|K/T|KÝ\s+THAY)\s*BỘ\s+TRƯỞNG\b"), "Thứ trưởng"),
        (re.compile(r"(?i)\b(?:KT[\./]|K/T|KÝ\s+THAY)\s*HIỆU\s+TRƯỞNG\b"), "Phó Hiệu trưởng"),
        (re.compile(r"(?i)\b(?:KT[\./]|K/T|KÝ\s+THAY)\s*TRƯỞNG\s+BAN\b"), "Phó Trưởng ban"),
        (re.compile(r"(?i)\b(?:KT[\./]|K/T|KÝ\s+THAY)\s*TRƯỞNG\s+PHÒNG\b"), "Phó Trưởng phòng"),
        (re.compile(r"(?i)\b(?:KT[\./]|K/T|KÝ\s+THAY)\s*VIỆN\s+TRƯỞNG\b"), "Phó Viện trưởng"),

        # Deputy roles without KT prefix
        (re.compile(r"(?i)\bPH[ÓỐ]\s+TỔNG\s+GIÁM\s+ĐỐC\b"), "Phó Tổng Giám đốc"),
        (re.compile(r"(?i)\bPH[ÓỐ]\s+GIÁM\s+ĐỐC\b"), "Phó Giám đốc"),
        (re.compile(r"(?i)\bPH[ÓỐ]\s+CHỦ\s+TỊCH\b"), "Phó Chủ tịch"),
        (re.compile(r"(?i)\bTHỨ\s+TRƯỞNG\b"), "Thứ trưởng"),
        (re.compile(r"(?i)\bPH[ÓỐ]\s+HIỆU\s+TRƯỞNG\b"), "Phó Hiệu trưởng"),
        (re.compile(r"(?i)\bPH[ÓỐ]\s+TRƯỞNG\s+BAN\b"), "Phó Trưởng ban"),
        (re.compile(r"(?i)\bPH[ÓỐ]\s+TRƯỞNG\s+PHÒNG\b"), "Phó Trưởng phòng"),
        (re.compile(r"(?i)\bPH[ÓỐ]\s+CHÁNH\s+VĂN\s+PHÒNG\b"), "Phó Chánh Văn phòng"),
        (re.compile(r"(?i)\bPH[ÓỐ]\s+VIỆN\s+TRƯỞNG\b"), "Phó Viện trưởng"),

        # Direct / Executive roles
        (re.compile(r"(?i)\b(?:TM[\./]|T/M)?\s*TỔNG\s+GIÁM\s+ĐỐC\b"), "Tổng Giám đốc"),
        (re.compile(r"(?i)\b(?:TM[\./]|T/M)?\s*GIÁM\s+ĐỐC\b"), "Giám đốc"),
        (re.compile(r"(?i)\b(?:TM[\./]|T/M)?\s*CHỦ\s+TỊCH\b"), "Chủ tịch"),
        (re.compile(r"(?i)\b(?:TM[\./]|T/M)?\s*BỘ\s+TRƯỞNG\b"), "Bộ trưởng"),
        (re.compile(r"(?i)\b(?:TM[\./]|T/M)?\s*HIỆU\s+TRƯỞNG\b"), "Hiệu trưởng"),
        (re.compile(r"(?i)\b(?:TM[\./]|T/M)?\s*TRƯỞNG\s+BAN\b"), "Trưởng ban"),
        (re.compile(r"(?i)\b(?:TM[\./]|T/M)?\s*TRƯỞNG\s+PHÒNG\b"), "Trưởng phòng"),
        (re.compile(r"(?i)\b(?:TL[\./]|T/L)?\s*CHÁNH\s+VĂN\s+PHÒNG\b"), "Chánh Văn phòng"),
        (re.compile(r"(?i)\b(?:TM[\./]|T/M)?\s*VIỆN\s+TRƯỞNG\b"), "Viện trưởng"),
    ]

    _EXCLUDE_NAME_WORDS: ClassVar[set[str]] = {
        "nơi", "nhận", "lưu", "vt", "r&d", "khtc", "tccb", "tổ", "chức",
        "cộng", "hòa", "xã", "hội", "chủ", "nghĩa", "việt", "nam",
        "độc", "lập", "tự", "do", "hạnh", "phúc", "đài", "tiếng", "nói",
        "ban", "hành", "quyết", "định", "điều", "khoản", "ngày", "tháng", "năm",
        "về", "việc", "kèm", "theo", "quy", "chế", "hướng", "dẫn", "thay",
        "mặt", "thừa", "lệnh", "ủy", "quyền", "ký", "tên", "chữ", "đóng", "dấu",
    }

    _SKIP_LINE_PREFIXES: ClassVar[tuple[str, ...]] = (
        "lưu:", "luru:", "nơi nhận:", "các ", "điều ", "theo ", "căn cứ ",
        "chánh ", "trưởng ", "giám đốc ", "tổng giám đốc ", "phó "
    )

    @classmethod
    def extract(cls, raw_text: str, filename: str = "") -> AdministrativeMetadata:
        """Extract metadata from raw document text using layout algorithms (zero hardcoding)."""
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        header_text = "\n".join(lines[:35]) if lines else ""
        tail_text = "\n".join(lines[-40:]) if lines else ""

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
    async def aextract_with_llm(cls, raw_text: str, filename: str = "") -> AdministrativeMetadata:
        """Extract metadata using LLM reasoning over document header and tail."""
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        header = "\n".join(lines[:30]) if lines else ""
        tail = "\n".join(lines[-30:]) if lines else ""

        prompt = (
            f"Hãy trích xuất metadata từ văn bản sau thành một khối JSON duy nhất với các trường:\n"
            f"- document_type: thể loại văn bản (ví dụ: Quyết định, Chỉ thị, Thông báo, Quy chế...)\n"
            f"- document_number: số hiệu văn bản (ví dụ: 1139/QĐ-TNVN)\n"
            f"- promulgation_date: ngày ban hành (định dạng YYYY-MM-DD hoặc null)\n"
            f"- issuing_authority: tên cơ quan ban hành (ví dụ: Đài Tiếng nói Việt Nam, Bộ Tài chính...)\n"
            f"- subject: trích yếu nội dung (về việc gì)\n"
            f"- signer_name: họ và tên chính xác của người ký văn bản (sửa lỗi OCR/chính tả nếu có)\n"
            f"- signer_role: chức danh của người ký (ví dụ: Tổng Giám đốc, Phó Tổng Giám đốc, Bộ trưởng...)\n\n"
            f"TÊN FILE GỐC (nếu có tham khảo): {filename}\n\n"
            f"--- PHẦN ĐẦU VĂN BẢN (HEADER) ---\n{header}\n\n"
            f"--- PHẦN CUỐI VĂN BẢN (TAIL) ---\n{tail}\n"
        )

        raw_llm = await cls._call_llm_stream(prompt)
        clean_json = re.sub(r"^```(?:json)?\s*", "", raw_llm.strip())
        clean_json = re.sub(r"\s*```$", "", clean_json.strip())
        data = json.loads(clean_json)

        return AdministrativeMetadata(
            document_number=data.get("document_number"),
            document_type=data.get("document_type") or "Quyết định",
            issuing_authority=data.get("issuing_authority"),
            promulgation_date=data.get("promulgation_date"),
            signer_name=data.get("signer_name"),
            signer_role=data.get("signer_role"),
            subject=data.get("subject"),
            confidence=0.95,
            extra_fields={"filename": filename, "extractor": "llm"} if filename else {"extractor": "llm"},
        )

    @classmethod
    async def aextract(
        cls,
        raw_text: str,
        filename: str = "",
        use_llm_fallback: bool = True,
    ) -> AdministrativeMetadata:
        """Hybrid metadata extraction: fast algorithmic parser first, fallback to LLM when incomplete."""
        meta = cls.extract(raw_text, filename=filename)

        # Return fast if algorithmic parser extracted complete core fields with high confidence
        if meta.signer_name and meta.document_number and meta.promulgation_date and meta.confidence >= 0.8:
            return meta

        if not use_llm_fallback:
            return meta

        try:
            llm_meta = await cls.aextract_with_llm(raw_text, filename=filename)
            return AdministrativeMetadata(
                document_number=meta.document_number or llm_meta.document_number,
                document_type=meta.document_type or llm_meta.document_type,
                issuing_authority=meta.issuing_authority or llm_meta.issuing_authority,
                promulgation_date=meta.promulgation_date or llm_meta.promulgation_date,
                signer_name=meta.signer_name or llm_meta.signer_name,
                signer_role=meta.signer_role or llm_meta.signer_role,
                subject=meta.subject or llm_meta.subject,
                confidence=max(meta.confidence, llm_meta.confidence),
                extra_fields={**meta.extra_fields, **llm_meta.extra_fields},
            )
        except Exception as exc:
            logger.warning("llm_metadata_extraction_fallback_failed error=%s", exc)
            return meta

    @classmethod
    async def _call_llm_stream(cls, prompt: str) -> str:
        api_key = str(settings.llm.openai_api_key or "")
        url = settings.llm.chat_completions_url()
        timeout = settings.timeouts.llm_request_seconds
        system_content = (
            "Bạn là chuyên gia trích xuất siêu dữ liệu (metadata) từ văn bản hành chính Việt Nam. "
            "Chỉ trả về duy nhất một khối JSON hợp lệ trong cặp ```json ``` hoặc thuần JSON, không giải thích thêm."
        )
        chunks: list[str] = []
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": settings.llm.primary_model,
                    "messages": [
                        {"role": "system", "content": system_content},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.0,
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        payload = json.loads(data_str)
                        choices = payload.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta", {}).get("content")
                            if delta:
                                chunks.append(delta)
                    except Exception:
                        continue
        return "".join(chunks).strip()

    @classmethod
    def _extract_document_type(cls, header_text: str, filename: str) -> str:
        fn_upper = filename.upper()
        if "_CT" in fn_upper or "CT_" in fn_upper or "CHITHI" in fn_upper:
            return "Chỉ thị"
        if "_QD" in fn_upper or "QD_" in fn_upper or "QUYETDINH" in fn_upper:
            return "Quyết định"

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

        pre_heading_lines: list[str] = []
        for line in header_text.splitlines():
            clean = line.strip()
            if re.match(r"(?i)^#*\s*(?:QUYẾT\s+ĐỊNH|CHỈ\s+THỊ|QUY\s+CHẾ)\b", clean):
                break
            if re.match(r"(?i)^-?\s*Căn\s+cứ\b", clean):
                break
            pre_heading_lines.append(clean)
        pre_heading_text = "\n".join(pre_heading_lines)

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
            if fn_num and fn_num != cleaned:
                m_fn = re.search(r"^\d+", fn_num)
                m_cl = re.search(r"^\d+", cleaned)
                if m_fn and m_cl:
                    fn_d, cl_d = m_fn.group(0), m_cl.group(0)
                    if fn_d.startswith(cl_d) and len(fn_d) > len(cl_d):
                        return fn_num
            return cleaned

        if fn_num:
            return fn_num

        return None

    @classmethod
    def _extract_number_from_filename(cls, filename: str, doc_type: str) -> str | None:
        if not filename:
            return None
        m_qd = re.search(r"_(\d+)(?:QD|QĐ)(?:_([A-ZĐ]+))?(?:_|\.|$)", filename, re.IGNORECASE)
        if m_qd:
            num = m_qd.group(1)
            suffix = m_qd.group(2) or "TNVN"
            return f"{num}/QĐ-{suffix}"
        m_ct = re.search(r"_(?:CT)?(\d+)(?:CT)?(?:_([A-ZĐ]+))?(?:_|\.|$)", filename, re.IGNORECASE)
        if m_ct and (doc_type == "Chỉ thị" or "CT" in filename.upper()):
            num = m_ct.group(1)
            suffix = m_ct.group(2) or "TNVN"
            return f"{num}/CT-{suffix}"
        return None

    @classmethod
    def _extract_promulgation_date(cls, header_text: str, filename: str) -> str | None:
        pre_heading_lines: list[str] = []
        for line in header_text.splitlines():
            clean = line.strip()
            if re.match(r"(?i)^-?\s*Căn\s+cứ\b", clean):
                break
            pre_heading_lines.append(clean)
        pre_text = "\n".join(pre_heading_lines)

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

        m_fn_date = re.search(r"_(\d{1,2})_(\d{1,2})_(\d{4})\.md$", filename, re.IGNORECASE)
        if m_fn_date:
            day, month, year = m_fn_date.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

        m_prefix_date = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})", filename)
        if m_prefix_date:
            day, month, year = m_prefix_date.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

        return None

    @classmethod
    def _extract_authority(cls, header_text: str) -> str | None:
        lines = [line.strip() for line in header_text.splitlines() if line.strip()]
        for line in lines[:15]:
            clean = re.sub(r"^[#\s\*_()\-]+|[#\s\*_()\-]+$", "", line).strip()
            if re.match(r"(?i)^(?:CỘNG\s+HÒA|ĐỘC\s+LẬP|QUYẾT\s+ĐỊNH|CHỈ\s+THỊ|QUY\s+CHẾ|THÔNG\s+BÁO)\b", clean):
                continue
            for pattern, canonical in cls.AUTHORITY_PREFIX_PATTERNS:
                if pattern.search(clean):
                    if canonical:
                        return canonical
                    m = pattern.search(clean)
                    if m:
                        return " ".join(w.capitalize() for w in m.group(0).split())
        return None

    @classmethod
    def _extract_subject(cls, header_text: str) -> str | None:
        """The "Về việc …" / "V/v …" line under the document type, above the basis.

        Only a line that *starts* with Về / VỀ / V/v (optionally after the
        type name, "QUYẾT ĐỊNH Về việc …") and comes before the first
        "Căn cứ" / "Điều 1" counts: "về việc" inside a cited decree
        ("Căn cứ Nghị định 152 … về việc quy định về phân cấp …") is never
        the subject of this document.
        """
        lines = [ln.strip(" \t#*_-+•") for ln in header_text.splitlines()]
        lines = [ln for ln in lines if ln]
        for idx, line in enumerate(lines):
            if _SUBJECT_STOP.match(line):
                return None
            m = _SUBJECT_LINE.match(line)
            prefix = m.group("prefix").strip(" :") if m else ""
            if m and prefix and not (prefix.isupper() and len(prefix) <= 40):
                m = None  # "… về việc" inside a sentence, not a subject line
            if not m:
                # "về …" in lower case directly under the type name ("QUYẾT ĐỊNH").
                lower = re.match(r"^(?:v/v|về)[\s.:]+(.+)$", line)
                if lower and idx > 0 and lines[idx - 1].isupper():
                    m = lower
            if not m:
                continue
            parts = [m.group(m.lastindex or 1).strip()]
            # Long subjects wrap onto lowercase continuation lines.
            for nxt in lines[idx + 1 : idx + 3]:
                if not nxt or not nxt[0].islower() or _SUBJECT_STOP.match(nxt):
                    break
                parts.append(nxt)
            clean = re.sub(r"\s+", " ", " ".join(parts)).strip(" ,.;")
            if not clean:
                continue
            if clean.lower().startswith("việc"):
                return f"Về {clean}"
            return f"Về việc {clean}"
        return None

    @classmethod
    def _extract_subject_from_article1(cls, text: str) -> str | None:
        """Fallback subject from the operative clause of Điều 1.

        Used when the title line was lost (OCR / layout): "Điều 1. Tặng Bằng
        khen của Tổng Giám đốc … cho …" → "Về việc tặng Bằng khen của Tổng
        Giám đốc …". Only the action is kept, never the recipients.
        """
        m = _ARTICLE_ONE.search(text)
        if not m:
            return None
        clause = re.sub(r"\s*\([^)]*\)?", "", m.group(1))  # "(danh sách kèm theo)", "(Loại 100"
        clause = _ARTICLE_ONE_CUT.split(clause.strip(), maxsplit=1)[0]
        clause = re.sub(r"(?i)\s+kèm theo (?:quyết định|chỉ thị) này", "", clause)
        clause = re.sub(r"\s+", " ", clause).strip(" .,;")
        # Only an operative clause names the decision ("Văn phòng, các đơn vị
        # …" is the list of executors, not what was decided).
        if not _ARTICLE_ONE_ACTION.search(clause) or len(clause.split()) < 2:
            return None
        if len(clause) > 160:
            head = clause[:160]
            # Prefer ending at the last listed item over mid-phrase.
            clause = head.rsplit(",", 1)[0] if head.rfind(",") > 80 else head.rsplit(" ", 1)[0]
        return f"Về việc {clause[0].lower()}{clause[1:]}"

    @classmethod
    def _extract_signatory(cls, tail_text: str) -> tuple[str | None, str | None]:
        """Extract signatory name and role algorithmically based on layout standards (Nghị định 30/2020/NĐ-CP)."""
        lines = [line.strip() for line in tail_text.splitlines() if line.strip()]
        detected_role: str | None = None
        role_idx: int = -1

        # Scan bottom-up for the last signature role indicator
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            for pat, role in cls.ROLE_SIGNATURE_PATTERNS:
                if pat.search(line):
                    detected_role = role
                    role_idx = idx
                    break
            if detected_role:
                break

        if not detected_role or role_idx < 0:
            return None, None

        # Search subsequent lines following the role block for the signer's name
        for line in lines[role_idx + 1:]:
            clean_line = re.sub(r"^[#\s\*_()\-<]+|[#\s\*_()\-/>]+$", "", line).strip()
            clean_line = re.sub(r"<[^>]+>", "", clean_line).strip()
            low_clean = clean_line.lower()

            if not clean_line or any(low_clean.startswith(p) for p in cls._SKIP_LINE_PREFIXES):
                continue
            if clean_line.endswith(":") or clean_line.endswith("."):
                continue
            if any(pat.search(clean_line) for pat, _ in cls.ROLE_SIGNATURE_PATTERNS):
                continue
            if re.match(
                r"(?i)^\(?(?:đã\s+ký|đã\s+kí|ký\s+tên|chữ\s+ký|ký,\s*đóng\s+dấu|ký,\s*ghi\s+rõ\s+họ\s+tên)\)?$",
                clean_line,
            ):
                continue

            words = clean_line.split()
            if 2 <= len(words) <= 5:
                lower_words = [w.lower() for w in words]
                if not any(w in cls._EXCLUDE_NAME_WORDS for w in lower_words):
                    # Validate that all words contain valid Vietnamese alphabetical characters
                    if all(re.match(r"^[A-ZĐÀ-Ỹa-zđà-ỹ]+$", w) for w in words):
                        signer_name = " ".join(w.capitalize() for w in words)
                        return signer_name, detected_role

        return None, detected_role
