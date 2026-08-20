"""Tool definitions and invocation handler for inspecting session spill artifacts."""

from typing import Any

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.errors import NotFoundError, ValidationError
from app.domain.models.tool import ToolDefinition, ToolResult
from app.services.spill import SpillStore
from app.tools.registry import ToolRegistry

SPILL_SLICE_TOOL = ToolDefinition(
    name="spill.slice",
    description=(
        "Fetch a slice of an oversized tool output that was spilled to storage. "
        "Specify the locator string, character offset, and character limit."
    ),
    category="spill",
    capabilities=["spill.read", "system.spill"],
    parameters_schema={
        "type": "object",
        "properties": {
            "locator": {
                "type": "string",
                "description": "The spill locator URI (e.g. 'spill://session/artifact_id') or file path.",
            },
            "offset": {
                "type": "integer",
                "description": "Zero-based character offset to start reading from.",
                "default": 0,
                "minimum": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of characters to return.",
                "default": 2000,
                "minimum": 1,
            },
        },
        "required": ["locator"],
    },
    risk_level=ActionRiskLevel.READ_ONLY,
    is_mutation=False,
    action_class=ActionClass.READ,
)

SPILL_FETCH_TOOL = ToolDefinition(
    name="spill.fetch",
    description="Alias for spill.slice. Read a slice of a spilled tool result by locator.",
    category="spill",
    capabilities=["spill.read", "system.spill"],
    parameters_schema=SPILL_SLICE_TOOL.parameters_schema,
    risk_level=ActionRiskLevel.READ_ONLY,
    is_mutation=False,
    action_class=ActionClass.READ,
)

SPILL_INFO_TOOL = ToolDefinition(
    name="spill.info",
    description="Get metadata (total bytes, characters, producing tool, creation time) of a spilled artifact.",
    category="spill",
    capabilities=["spill.read", "system.spill"],
    parameters_schema={
        "type": "object",
        "properties": {
            "locator": {
                "type": "string",
                "description": "The spill locator URI or path to inspect.",
            },
        },
        "required": ["locator"],
    },
    risk_level=ActionRiskLevel.READ_ONLY,
    is_mutation=False,
    action_class=ActionClass.READ,
)

SPILL_TOOL_DEFINITIONS: list[ToolDefinition] = [
    SPILL_SLICE_TOOL,
    SPILL_FETCH_TOOL,
    SPILL_INFO_TOOL,
]


class SpillInspectionTools:
    """Execution handler for inspecting spilled tool outputs."""

    def __init__(self, store: SpillStore) -> None:
        self.store = store

    def slice_spill(self, locator: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
        """Read a character slice of the spilled text."""
        if not locator or not locator.strip():
            raise ValidationError("Locator must not be blank.")
        text_slice = self.store.read_text(locator.strip(), offset=offset, limit=limit)
        ref = self.store.get_ref(locator.strip())
        return {
            "locator": locator.strip(),
            "offset": offset,
            "limit": limit,
            "returned_characters": len(text_slice),
            "total_characters": ref.character_count if ref else None,
            "total_bytes": ref.byte_count if ref else None,
            "content": text_slice,
        }

    def fetch_spill(self, locator: str, offset: int = 0, limit: int = 2000) -> dict[str, Any]:
        """Alias for slice_spill."""
        return self.slice_spill(locator, offset=offset, limit=limit)

    def get_info(self, locator: str) -> dict[str, Any]:
        """Retrieve metadata for a spill locator."""
        if not locator or not locator.strip():
            raise ValidationError("Locator must not be blank.")
        ref = self.store.get_ref(locator.strip())
        if not ref:
            raise NotFoundError(
                f"Spill artifact '{locator}' not found.",
                details={"locator": locator},
            )
        return {
            "locator": ref.locator,
            "session_id": ref.session_id,
            "artifact_id": ref.artifact_id,
            "byte_count": ref.byte_count,
            "character_count": ref.character_count,
            "tool_name": ref.tool_name,
            "call_id": ref.call_id,
            "created_at": ref.created_at.isoformat(),
            "retrieval_hint": ref.retrieval_hint,
        }


def build_spill_tool_registry() -> ToolRegistry:
    """Create a registry pre-populated with spill inspection tool definitions."""
    return ToolRegistry(SPILL_TOOL_DEFINITIONS)


__all__ = [
    "SPILL_FETCH_TOOL",
    "SPILL_INFO_TOOL",
    "SPILL_SLICE_TOOL",
    "SPILL_TOOL_DEFINITIONS",
    "SpillInspectionTools",
    "build_spill_tool_registry",
]
