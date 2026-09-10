"""Structured report return channel (spec P11-10).

Specialists conclude by calling the dedicated ``specialist.report`` tool.
This module validates the payload against :class:`SpecialistReport` and maps
terminal stop reasons to synthesized reports when the LLM never reports.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.domain.enums import SpecialistStatus, StopReason
from app.domain.models.platform.tool import ToolDefinition
from app.domain.models.supervisor.specialist import SpecialistReport, ToolCallRequest

REPORT_TOOL_NAME = "specialist.report"

REPORT_TOOL_DEFINITION = ToolDefinition(
    name=REPORT_TOOL_NAME,
    description=(
        "Conclude the specialist activation with a structured report. "
        "Call exactly once when the goal is achieved, blocked, or needs "
        "more context or approval."
    ),
    category="specialist",
    capabilities=["specialist.report"],
    parameters_schema={
        "type": "object",
        "required": ["status", "summary"],
        "properties": {
            "status": {
                "type": "string",
                "enum": ["success", "blocked", "needs_more_context", "needs_approval"],
            },
            "summary": {"type": "string"},
            "data": {"type": "object"},
            "blockers": {"type": "array", "items": {"type": "string"}},
            "missing_context": {"type": "array", "items": {"type": "string"}},
        },
    },
)


class ReportValidationError(ValueError):
    """Raised when a report tool call fails schema validation (becomes an observation)."""


def parse_report_call(call: ToolCallRequest) -> SpecialistReport:
    """Validate a ``specialist.report`` invocation into a structured report."""
    if call.tool_name != REPORT_TOOL_NAME:
        raise ReportValidationError(f"Not a report call: '{call.tool_name}'.")
    try:
        return SpecialistReport.model_validate(call.arguments)
    except ValidationError as exc:
        raise ReportValidationError(f"Invalid report payload: {exc.errors()}") from exc


def report_from_stop(stop_reason: StopReason, summary: str) -> SpecialistReport:
    """Synthesize a terminal report when the loop stops without a valid report."""
    status = (
        SpecialistStatus.SUCCESS if stop_reason is StopReason.SUCCESS else SpecialistStatus.BLOCKED
    )
    return SpecialistReport(status=status, summary=summary, blockers=[summary])


def coerce_report_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Best-effort normalization of a raw report payload before validation."""
    coerced = dict(arguments)
    status = coerced.get("status")
    if isinstance(status, str):
        coerced["status"] = status.strip().lower()
    return coerced
