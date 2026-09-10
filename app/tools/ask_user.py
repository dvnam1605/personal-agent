"""Tool definition and helper for asking structured user questions (spec P18-02A).

Enables the model to request user feedback on draft plans or ask structured multiple-choice
questions rather than relying exclusively on open-ended conversational output.
"""

from __future__ import annotations

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.models.platform.tool import (
    ToolContext,
    ToolDefinition,
    ToolExecutionMetadata,
    ToolInput,
    ToolResult,
)

ASK_USER_TOOL_NAME = "tool_ask_user"
ASK_USER_ALIAS_NAME = "ask_user"

ASK_USER_TOOL = ToolDefinition(
    name=ASK_USER_TOOL_NAME,
    description=(
        "Ask the user one or more structured multiple-choice or clarification questions, "
        "or request feedback on a proposed execution plan."
    ),
    category="interactive",
    capabilities=["interactive.ask_user", "system.interactive"],
    parameters_schema={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "List of structured question items to ask the user.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Unique identifier for this question item.",
                        },
                        "question": {
                            "type": "string",
                            "description": "The question prompt displayed to the user.",
                        },
                        "detail": {
                            "type": ["string", "null"],
                            "description": "Optional detailed context or markdown explanation.",
                        },
                        "options": {
                            "type": ["array", "null"],
                            "description": "Optional list of choices for the user to select from.",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": ["string", "null"]},
                                },
                                "required": ["label"],
                            },
                        },
                        "multi_select": {
                            "type": "boolean",
                            "description": "Whether multiple options can be chosen.",
                            "default": False,
                        },
                        "intent": {
                            "type": ["string", "null"],
                            "description": "Special UI presentation intent (e.g. 'plan-review').",
                            "enum": ["plan-review", None],
                        },
                    },
                    "required": ["id", "question"],
                },
                "minItems": 1,
            }
        },
        "required": ["questions"],
    },
    risk_level=ActionRiskLevel.READ_ONLY,
    is_mutation=False,
    action_class=ActionClass.READ,
)

ASK_USER_ALIAS_TOOL = ToolDefinition(
    name=ASK_USER_ALIAS_NAME,
    description=ASK_USER_TOOL.description,
    category=ASK_USER_TOOL.category,
    capabilities=ASK_USER_TOOL.capabilities,
    parameters_schema=ASK_USER_TOOL.parameters_schema,
    risk_level=ASK_USER_TOOL.risk_level,
    is_mutation=ASK_USER_TOOL.is_mutation,
    action_class=ASK_USER_TOOL.action_class,
)


def ask_user_tool_definitions() -> list[ToolDefinition]:
    """Return tool definitions for the Question Plane."""
    return [ASK_USER_TOOL, ASK_USER_ALIAS_TOOL]


async def execute_ask_user(tool_input: ToolInput, context: ToolContext) -> ToolResult:
    """Execute ask_user tool by triggering an interrupt or returning structured question payload (spec P18-02A, M2)."""
    import time

    from app.harness.interrupts import interrupt_for_question

    started = time.perf_counter()
    raw_questions = (
        tool_input.arguments.get("questions") if isinstance(tool_input.arguments, dict) else None
    )
    if not isinstance(raw_questions, list) or not raw_questions:
        return ToolResult(
            tool_name=tool_input.tool_name,
            success=False,
            error="Missing or empty 'questions' array in tool arguments.",
            metadata=ToolExecutionMetadata(tool_name=tool_input.tool_name, latency_ms=0.0),
        )

    if len(raw_questions) > 5:
        return ToolResult(
            tool_name=tool_input.tool_name,
            success=False,
            error=f"Too many question items in ask_user (maximum 5, got {len(raw_questions)}).",
            metadata=ToolExecutionMetadata(tool_name=tool_input.tool_name, latency_ms=0.0),
        )

    for idx, q_item in enumerate(raw_questions):
        if not isinstance(q_item, dict):
            return ToolResult(
                tool_name=tool_input.tool_name,
                success=False,
                error=f"Question item {idx} must be a dictionary.",
                metadata=ToolExecutionMetadata(tool_name=tool_input.tool_name, latency_ms=0.0),
            )
        q_text = str(q_item.get("question") or "")
        if not q_text or len(q_text) > 1000:
            return ToolResult(
                tool_name=tool_input.tool_name,
                success=False,
                error=f"Question item {idx} text must be non-empty and <= 1000 chars.",
                metadata=ToolExecutionMetadata(tool_name=tool_input.tool_name, latency_ms=0.0),
            )
        opts = q_item.get("options")
        if opts is not None:
            if not isinstance(opts, list) or len(opts) > 10:
                return ToolResult(
                    tool_name=tool_input.tool_name,
                    success=False,
                    error=f"Question item {idx} options must be a list with at most 10 items.",
                    metadata=ToolExecutionMetadata(tool_name=tool_input.tool_name, latency_ms=0.0),
                )

    payload = {
        "run_id": context.run_id,
        "questions": raw_questions,
    }
    try:
        resumed = interrupt_for_question(payload)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return ToolResult(
            tool_name=tool_input.tool_name,
            success=True,
            output=resumed,
            metadata=ToolExecutionMetadata(tool_name=tool_input.tool_name, latency_ms=elapsed_ms),
        )
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return ToolResult(
            tool_name=tool_input.tool_name,
            success=False,
            error=f"Interactive question flow failed: {exc}",
            metadata=ToolExecutionMetadata(tool_name=tool_input.tool_name, latency_ms=elapsed_ms),
        )


__all__ = [
    "ASK_USER_ALIAS_NAME",
    "ASK_USER_ALIAS_TOOL",
    "ASK_USER_TOOL",
    "ASK_USER_TOOL_NAME",
    "ask_user_tool_definitions",
    "execute_ask_user",
]
