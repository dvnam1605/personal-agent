"""Least-privilege invariants for WF-05 Meeting Prep (read-only tools only)."""

from __future__ import annotations

from app.domain.errors import PermissionDeniedError

FORBIDDEN_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "calendar.create_event",
        "calendar.update_event",
        "calendar.delete_event",
        "calendar.add_attendee",
        "calendar.remove_attendee",
        "gmail.create_draft",
        "gmail.update_draft",
        "gmail.delete_draft",
        "gmail.send_draft",
        "gmail.reply",
        "gmail.forward",
        "gmail.archive",
        "gmail.trash",
        "gmail.add_label",
        "gmail.remove_label",
        "drive.upload_file",
        "drive.create_folder",
        "drive.move_file",
        "drive.rename_file",
        "drive.delete_file",
        "drive.update_permissions",
    }
)


def assert_read_only_tool(tool_name: str) -> None:
    """Verify tool is strictly read-only and not in forbidden mutation set."""
    name = tool_name.strip()
    if name in FORBIDDEN_MUTATION_TOOLS or any(
        kw in name for kw in ("create", "delete", "send", "update", "upload", "trash", "remove")
    ):
        raise PermissionDeniedError(
            f"Tool '{tool_name}' is forbidden in WF-05 MeetingPrepGraph. "
            "Least-privilege enforces read-only access.",
            details={"tool_name": tool_name, "workflow_id": "WF-05"},
        )
