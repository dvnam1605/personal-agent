"""Per-category extra fields stored in ``document_profiles.extras``.

Core fields every kind shares live in typed columns; anything specific to
one kind is validated here and stored as JSONB. Unknown keys are dropped so
an over-eager LLM cannot smuggle arbitrary structure into the profile.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.services.ingestion.profiling.categories import DocCategory


class _Extras(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AdminExtras(_Extras):
    signer: str | None = None
    signer_role: str | None = None
    doc_type: str | None = None
    replaces: list[str] = []


class ReportExtras(_Extras):
    period: str | None = None
    reporting_unit: str | None = None
    reporter: str | None = None


class ContractExtras(_Extras):
    parties: list[str] = []
    value: str | None = None
    currency: str | None = None
    effective_date: str | None = None
    expiry_date: str | None = None


class MinutesExtras(_Extras):
    meeting_date: str | None = None
    location: str | None = None
    chair: str | None = None
    attendees: list[str] = []
    decisions: list[str] = []


class TechnicalExtras(_Extras):
    product: str | None = None
    version: str | None = None
    authors: list[str] = []
    isbn: str | None = None


class AcademicExtras(_Extras):
    authors: list[str] = []
    student_id: str | None = None
    supervisor: str | None = None
    program: str | None = None
    institution: str | None = None
    faculty: str | None = None
    year: int | None = None


class SpreadsheetExtras(_Extras):
    sheets: list[str] = []
    row_counts: dict[str, int] = {}


EXTRAS_MODELS: dict[DocCategory, type[_Extras]] = {
    DocCategory.ADMINISTRATIVE: AdminExtras,
    DocCategory.REPORT: ReportExtras,
    DocCategory.CONTRACT: ContractExtras,
    DocCategory.MINUTES: MinutesExtras,
    DocCategory.TECHNICAL: TechnicalExtras,
    DocCategory.ACADEMIC: AcademicExtras,
    DocCategory.SPREADSHEET: SpreadsheetExtras,
}


def validate_extras(category: DocCategory, data: dict[str, Any]) -> dict[str, Any]:
    """Coerce ``data`` to the category's extras model, dropping empties."""
    model = EXTRAS_MODELS.get(category)
    if model is None:
        return {}
    try:
        dumped = model.model_validate(data).model_dump()
    except ValueError:
        # Keep whatever fields individually validate rather than losing all.
        dumped = {}
        for key, value in data.items():
            try:
                dumped.update(model.model_validate({key: value}).model_dump(include={key}))
            except ValueError:
                continue
    return {k: v for k, v in dumped.items() if v not in (None, [], {}, "")}
