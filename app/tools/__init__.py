"""Tool declarations, registries, and runtime capability views."""

from app.domain.models import ToolDefinition, ToolRestriction
from app.tools.ask_user import (
    ASK_USER_ALIAS_NAME,
    ASK_USER_ALIAS_TOOL,
    ASK_USER_TOOL,
    ASK_USER_TOOL_NAME,
    ask_user_tool_definitions,
)
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
from app.tools.knowledge import (
    KNOWLEDGE_TOOL_DEFINITIONS,
    RETRIEVAL_TOOL_DEFINITIONS,
    WEB_TOOL_DEFINITIONS,
    MockWebSearchProvider,
    RetrievalTools,
    WebSearchProvider,
    WebSearchTools,
    build_knowledge_tool_registry,
    knowledge_tool_definitions,
)
from app.tools.registry import (
    MutationClassifier,
    ReadOnlyToolRegistry,
    ScopedToolView,
    ToolRegistry,
    ToolRegistryView,
    capability_matches,
)
from app.tools.spill_tools import (
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
    "KNOWLEDGE_TOOL_DEFINITIONS",
    "RETRIEVAL_TOOL_DEFINITIONS",
    "MockWebSearchProvider",
    "ReadOnlyToolRegistry",
    "RetrievalTools",
    "WEB_TOOL_DEFINITIONS",
    "WebSearchProvider",
    "WebSearchTools",
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
    "build_knowledge_tool_registry",
    "build_spill_tool_registry",
    "capability_matches",
    "communication_tool_definitions",
    "calendar_tool_definitions",
    "drive_tool_definitions",
    "knowledge_tool_definitions",
    "ask_user_tool_definitions",
    "ASK_USER_TOOL",
    "ASK_USER_TOOL_NAME",
    "ASK_USER_ALIAS_TOOL",
    "ASK_USER_ALIAS_NAME",
]
