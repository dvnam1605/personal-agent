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


PROPOSE_TOOL_NAME = "specialist.propose_action"
PROPOSABLE_TOOLS = ("gmail.create_draft",)

PROPOSE_TOOL_DEFINITION = ToolDefinition(
    name=PROPOSE_TOOL_NAME,
    description=(
        "Propose a write you are not allowed to run yourself (for example an email draft). "
        "It ends your task; a person reviews and approves it before anything is written. "
        "For gmail.create_draft give arguments {to: [emails], subject, body}."
    ),
    category="specialist",
    capabilities=["specialist.propose"],
    parameters_schema={
        "type": "object",
        "required": ["tool_name", "arguments"],
        "properties": {
            "tool_name": {"type": "string", "enum": list(PROPOSABLE_TOOLS)},
            "arguments": {
                "type": "object",
                "required": ["to", "subject", "body"],
                "properties": {
                    "to": {"type": "array", "items": {"type": "string"}},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
            },
            "summary": {"type": "string"},
        },
    },
)


def report_from_proposal(call: ToolCallRequest) -> SpecialistReport:
    """Turn a ``specialist.propose_action`` call into a needs-approval report."""
    tool_name = str(call.arguments.get("tool_name") or "")
    arguments = call.arguments.get("arguments")
    if tool_name not in PROPOSABLE_TOOLS or not isinstance(arguments, dict):
        raise ReportValidationError(
            f"Only {', '.join(PROPOSABLE_TOOLS)} can be proposed, with an arguments object."
        )
    missing = [key for key in ("to", "subject", "body") if not arguments.get(key)]
    if missing:
        raise ReportValidationError(f"Proposal arguments are missing: {', '.join(missing)}.")
    summary = str(call.arguments.get("summary") or f"Proposed {tool_name} for approval.")
    return SpecialistReport(
        status=SpecialistStatus.NEEDS_APPROVAL,
        summary=summary,
        data={"proposed_action": {"tool_name": tool_name, "arguments": arguments}},
    )
