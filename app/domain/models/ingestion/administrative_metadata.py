"""Administrative metadata contracts for Vietnamese administrative documents.

Adheres to Vietnamese governmental drafting standards (Nghị định 30/2020/NĐ-CP)
for Quyết định (Decisions), Chỉ thị (Directives), Quy chế (Regulations), etc.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AdministrativeMetadata(BaseModel):
    """Structured attributes extracted from administrative documents."""

    model_config = ConfigDict(frozen=True)

    document_number: str | None = Field(
        default=None,
        description="Số ký hiệu văn bản (e.g., '427/QĐ-TNVN', '80-QĐ/TNVN', '1838/CT-TNVN')",
    )
    document_type: str | None = Field(
        default=None,
        description="Tên loại văn bản (e.g., 'Quyết định', 'Chỉ thị', 'Quy chế', 'Thông báo')",
    )
    issuing_authority: str | None = Field(
        default="Đài Tiếng nói Việt Nam",
        description="Cơ quan, tổ chức ban hành văn bản",
    )
    promulgation_date: str | None = Field(
        default=None,
        description="Ngày tháng năm ban hành văn bản định dạng ISO YYYY-MM-DD",
    )
    signer_name: str | None = Field(
        default=None,
        description="Họ tên người ký văn bản (e.g., 'Vũ Hải Quang', 'Đỗ Tiến Sỹ', 'Ngô Minh Hiển')",
    )
    signer_role: str | None = Field(
        default=None,
        description="Chức vụ người ký (e.g., 'Tổng Giám đốc', 'Phó Tổng Giám đốc')",
    )
    subject: str | None = Field(
        default=None,
        description="Trích yếu nội dung văn bản (e.g., 'Về việc phê duyệt dự toán kinh phí...')",
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Độ tin cậy của thuật toán bóc tách",
    )
    extra_fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Thuộc tính mở rộng khác (nơi nhận, cơ sở pháp lý...)",
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary for chunk and document storage."""
        return {
            "document_number": self.document_number,
            "document_type": self.document_type,
            "issuing_authority": self.issuing_authority,
            "promulgation_date": self.promulgation_date,
            "signer_name": self.signer_name,
            "signer_role": self.signer_role,
            "subject": self.subject,
            "confidence": self.confidence,
            **self.extra_fields,
        }
