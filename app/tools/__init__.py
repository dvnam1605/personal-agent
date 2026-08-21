"""Tool declarations, registries, and runtime capability views."""

from app.domain.models import ToolDefinition, ToolRestriction
from app.tools.google_calendar import (
    CALENDAR_TOOL_DEFINITIONS,
    GoogleCalendarTools,
    build_calendar_tool_registry,
    calendar_tool_definitions,
)
from app.tools.google_communication import (
    COMMUNICATION_TOOL_DEFINITIONS,
    GoogleCommunicationTools,
    build_communication_tool_registry,
    communication_tool_definitions,
)
from app.tools.google_drive import (
    DRIVE_TOOL_DEFINITIONS,
    GoogleDriveTools,
    build_drive_tool_registry,
    drive_tool_definitions,
)
from app.tools.registry import (
    MutationClassifier,
    ReadOnlyToolRegistry,
    ScopedToolView,
    ToolRegistry,
    ToolRegistryView,
    capability_matches,
)
from app.tools.spill import (
    SPILL_FETCH_TOOL,
    SPILL_INFO_TOOL,
    SPILL_SLICE_TOOL,
    SPILL_TOOL_DEFINITIONS,
    SpillInspectionTools,
    build_spill_tool_registry,
)

__all__ = [
    "MutationClassifier",
    "COMMUNICATION_TOOL_DEFINITIONS",
    "CALENDAR_TOOL_DEFINITIONS",
    "DRIVE_TOOL_DEFINITIONS",
    "GoogleCalendarTools",
    "GoogleCommunicationTools",
    "GoogleDriveTools",
    "ReadOnlyToolRegistry",
    "SPILL_FETCH_TOOL",
    "SPILL_INFO_TOOL",
    "SPILL_SLICE_TOOL",
    "SPILL_TOOL_DEFINITIONS",
    "ScopedToolView",
    "SpillInspectionTools",
    "ToolDefinition",
    "ToolRegistry",
    "ToolRegistryView",
    "ToolRestriction",
    "build_communication_tool_registry",
    "build_calendar_tool_registry",
    "build_drive_tool_registry",
    "build_spill_tool_registry",
    "capability_matches",
    "communication_tool_definitions",
    "calendar_tool_definitions",
    "drive_tool_definitions",
]
