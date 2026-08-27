"""Tool definitions and invocation handler for inspecting session spill artifacts."""

from typing import Any

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.domain.models.tool import ToolDefinition
from app.services.spill import SpillStore
from app.tools.registry import ToolRegistry

SPILL_LOCATOR_SCHEMA = {
    "type": "string",
    "description": "The spill locator URI of the artifact (e.g. 'spill://session/artifact_id').",
}
SPILL_SESSION_SCHEMA = {
    "type": "string",
    "description": "Session ID of the caller; the locator must belong to this session.",
}

SPILL_SLICE_TOOL = ToolDefinition(
    name="spill.slice",
    description=(
        "Fetch a slice of an oversized tool output that was spilled to storage. "
        "Specify the session ID, the locator string, character offset, and character limit."
    ),
    category="spill",
    capabilities=["spill.read", "system.spill"],
    parameters_schema={
        "type": "object",
        "properties": {
            "session_id": SPILL_SESSION_SCHEMA,
            "locator": SPILL_LOCATOR_SCHEMA,
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
        "required": ["session_id", "locator"],
    },
    risk_level=ActionRiskLevel.READ_ONLY,
    is_mutation=False,
    action_class=ActionClass.READ,
)

SPILL_FETCH_TOOL = ToolDefinition(
    name="spill.fetch",
    description=(
        "Alias for spill.slice. Read a slice of a spilled tool result by session and locator."
    ),
    category="spill",
    capabilities=["spill.read", "system.spill"],
    parameters_schema=SPILL_SLICE_TOOL.parameters_schema,
    risk_level=ActionRiskLevel.READ_ONLY,
    is_mutation=False,
    action_class=ActionClass.READ,
)

SPILL_INFO_TOOL = ToolDefinition(
    name="spill.info",
    description=(
        "Get metadata (total bytes, characters, producing tool, creation time) of a spilled "
        "artifact belonging to the caller's session."
    ),
    category="spill",
    capabilities=["spill.read", "system.spill"],
    parameters_schema={
        "type": "object",
        "properties": {
            "session_id": SPILL_SESSION_SCHEMA,
            "locator": SPILL_LOCATOR_SCHEMA,
        },
        "required": ["session_id", "locator"],
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


def _require_locator_session(locator: str, session_id: str) -> None:
    """Enforce per-session spill isolation at the tool boundary.

    Only ``spill://<session>/<artifact>`` locators owned by ``session_id`` may be
    inspected, so one execution can never read another session's spilled output.
    """
    if not session_id or not session_id.strip():
        raise ValidationError("session_id must be provided to inspect spill artifacts.")
    normalized = locator.strip()
    if not normalized.startswith("spill://"):
        raise ValidationError(
            "Spill tools accept 'spill://session/artifact' locators only.",
            details={"locator": locator},
        )
    owner_session = normalized[len("spill://") :].split("/", 1)[0]
    if owner_session != session_id.strip():
        raise PermissionDeniedError(
            "Spill artifacts are isolated per session.",
            details={"locator": locator, "requested_session": owner_session},
        )


class SpillInspectionTools:
    """Execution handler for inspecting spilled tool outputs."""

    def __init__(self, store: SpillStore) -> None:
        self.store = store

    def slice_spill(
        self, locator: str, offset: int = 0, limit: int = 2000, *, session_id: str
    ) -> dict[str, Any]:
        """Read a character slice of a spill artifact owned by ``session_id``."""
        _require_locator_session(locator, session_id)
        normalized = locator.strip()
        text_slice = self.store.read_text(normalized, offset=offset, limit=limit)
        ref = self.store.get_ref(normalized)
        return {
            "locator": normalized,
            "offset": offset,
            "limit": limit,
            "returned_characters": len(text_slice),
            "total_characters": ref.character_count if ref else None,
            "total_bytes": ref.byte_count if ref else None,
            "content": text_slice,
        }

    def fetch_spill(
        self, locator: str, offset: int = 0, limit: int = 2000, *, session_id: str
    ) -> dict[str, Any]:
        """Alias for slice_spill."""
        return self.slice_spill(locator, offset=offset, limit=limit, session_id=session_id)

    def get_info(self, locator: str, *, session_id: str) -> dict[str, Any]:
        """Retrieve metadata for a spill locator owned by ``session_id``."""
        _require_locator_session(locator, session_id)
        normalized = locator.strip()
        ref = self.store.get_ref(normalized)
        if not ref:
            raise NotFoundError(
                f"Spill artifact '{normalized}' not found.",
                details={"locator": normalized},
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
