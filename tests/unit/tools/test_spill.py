"""Unit tests for P4-08 tool output spill policy and storage."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.domain.models import ToolExecutionMetadata, ToolResult
from app.domain.models.platform.spill import SpillPolicyConfig
from app.services.platform.spill import InMemorySpillStore, LocalFileSpillStore, SpillPolicy
from app.tools.spill_tools import (
    SpillInspectionTools,
    build_spill_tool_registry,
)


def _make_tool_result(
    output: str | dict | list | None,
    tool_name: str = "gmail.search",
    success: bool = True,
    error: str | None = None,
) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        success=success,
        output=output,
        error=error,
        metadata=ToolExecutionMetadata(
            tool_name=tool_name,
            latency_ms=12.5,
            cached=False,
            retry_count=0,
            timestamp=datetime.now(UTC),
        ),
    )


def test_in_memory_spill_store_lifecycle() -> None:
    store = InMemorySpillStore()
    content = "Hello, world! This is a long spilled text." * 50
    session_id = "session-123"

    ref = store.save_text(
        session_id=session_id,
        content=content,
        tool_name="gmail.search",
        suggested_name="emails.txt",
    )

    assert ref.session_id == "session-123"
    assert ref.byte_count == len(content.encode("utf-8"))
    assert ref.character_count == len(content)
    assert ref.locator.startswith("spill://session-123/")

    # Read full and sliced
    assert store.read_text(ref.locator) == content
    assert store.read_text(ref.locator, offset=0, limit=5) == "Hello"
    assert store.read_text(ref.locator, offset=7, limit=5) == "world"

    # Metadata retrieval
    retrieved_ref = store.get_ref(ref.locator)
    assert retrieved_ref is not None
    assert retrieved_ref.artifact_id == ref.artifact_id

    # List session spills
    spills = store.list_spills("session-123")
    assert len(spills) == 1
    assert spills[0].locator == ref.locator

    # Unknown locator raises NotFoundError
    with pytest.raises(NotFoundError):
        store.read_text("spill://session-123/nonexistent")


def test_in_memory_spill_store_validation() -> None:
    store = InMemorySpillStore()
    with pytest.raises(ValidationError, match="session_id"):
        store.save_text(session_id="", content="test")


def test_local_file_spill_store(tmp_path: Path) -> None:
    store = LocalFileSpillStore(base_dir=tmp_path / "spills")
    content = "File based spill content line 1\nLine 2\nLine 3"
    session_id = "session-abc"

    ref = store.save_text(
        session_id=session_id,
        content=content,
        tool_name="calendar.list_events",
        suggested_name="events",
    )

    assert ref.locator.startswith("spill://session-abc/")
    assert store.read_text(ref.locator) == content
    assert store.read_text(ref.locator, offset=0, limit=4) == "File"

    retrieved_ref = store.get_ref(ref.locator)
    assert retrieved_ref is not None
    assert retrieved_ref.byte_count == len(content.encode("utf-8"))

    spills = store.list_spills("session-abc")
    assert len(spills) == 1


def test_local_file_spill_store_path_traversal_prevention(tmp_path: Path) -> None:
    store = LocalFileSpillStore(base_dir=tmp_path / "spills")
    with pytest.raises(ValidationError, match="Security violation|Invalid spill URI"):
        store.read_text("spill://session-abc/../../etc/passwd")


def test_spill_policy_below_threshold_is_untouched() -> None:
    store = InMemorySpillStore()
    policy = SpillPolicy(store, SpillPolicyConfig(max_inline_bytes=200))
    short_content = "Small tool result"
    result = _make_tool_result(output=short_content)

    processed = policy.process_tool_result(result, session_id="sess-1")
    assert processed.output == short_content
    assert len(store.list_spills("sess-1")) == 0


def test_spill_policy_above_threshold_triggers_spill_and_bounds_preview() -> None:
    store = InMemorySpillStore()
    config = SpillPolicyConfig(max_inline_bytes=300)
    policy = SpillPolicy(store, config)

    large_text = "Alpha Start. " + ("Middle content repeating. " * 30) + " Omega End."
    result = _make_tool_result(output=large_text)

    processed = policy.process_tool_result(result, session_id="sess-1", call_id="call-42")

    # The result output is a bounded string containing head, tail, and locator notice
    output_str = processed.output
    assert isinstance(output_str, str)
    assert "Alpha Start." in output_str
    assert "Omega End." in output_str
    assert "Omitted" in output_str
    assert "Full formatted result stored at: spill://sess-1/" in output_str

    # Whole replacement UTF-8 bytes must not exceed max_inline_bytes
    assert len(output_str.encode("utf-8")) <= config.max_inline_bytes

    # Spill store contains full text
    spills = store.list_spills("sess-1")
    assert len(spills) == 1
    assert store.read_text(spills[0].locator) == large_text


def test_spill_policy_structured_dict_output() -> None:
    store = InMemorySpillStore()
    config = SpillPolicyConfig(max_inline_bytes=250)
    policy = SpillPolicy(store, config)

    large_dict = {"items": [{"id": i, "name": f"Item {i}", "data": "x" * 20} for i in range(20)]}
    result = _make_tool_result(output=large_dict)

    processed = policy.process_tool_result(result, session_id="sess-1")
    assert isinstance(processed.output, str)
    assert "Omitted" in processed.output
    assert "spill://sess-1/" in processed.output


def test_spill_policy_excluded_tools_are_not_spilled() -> None:
    store = InMemorySpillStore()
    policy = SpillPolicy(store, SpillPolicyConfig(max_inline_bytes=100))

    large_text = "X" * 500
    result = _make_tool_result(output=large_text, tool_name="spill.slice")

    processed = policy.process_tool_result(result, session_id="sess-1")
    assert processed.output == large_text
    assert len(store.list_spills("sess-1")) == 0


def test_spill_policy_best_effort_on_missing_session() -> None:
    store = InMemorySpillStore()
    policy = SpillPolicy(store, SpillPolicyConfig(max_inline_bytes=100))

    large_text = "X" * 500
    result = _make_tool_result(output=large_text)

    # Missing session_id logs warning and leaves result inline
    processed = policy.process_tool_result(result, session_id=None)
    assert processed.output == large_text


def test_spill_policy_disabled_config() -> None:
    store = InMemorySpillStore()
    policy = SpillPolicy(store, SpillPolicyConfig(enabled=False, max_inline_bytes=100))

    large_text = "X" * 500
    result = _make_tool_result(output=large_text)

    processed = policy.process_tool_result(result, session_id="sess-1")
    assert processed.output == large_text


def test_spill_inspection_tools() -> None:
    store = InMemorySpillStore()
    content = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    ref = store.save_text(session_id="sess-inspect", content=content, tool_name="test")

    tools = SpillInspectionTools(store)

    # Slice
    sliced = tools.slice_spill(locator=ref.locator, offset=10, limit=5, session_id="sess-inspect")
    assert sliced["content"] == "ABCDE"
    assert sliced["offset"] == 10
    assert sliced["limit"] == 5
    assert sliced["total_characters"] == len(content)

    # Fetch alias
    fetched = tools.fetch_spill(locator=ref.locator, offset=0, limit=10, session_id="sess-inspect")
    assert fetched["content"] == "0123456789"

    # Info
    info = tools.get_info(locator=ref.locator, session_id="sess-inspect")
    assert info["locator"] == ref.locator
    assert info["character_count"] == len(content)
    assert info["session_id"] == "sess-inspect"

    # Error handling
    with pytest.raises(ValidationError):
        tools.slice_spill(locator="", session_id="sess-inspect")
    with pytest.raises(NotFoundError):
        tools.get_info(locator="spill://sess-inspect/not-there", session_id="sess-inspect")


def test_spill_inspection_rejects_cross_session_access() -> None:
    store = InMemorySpillStore()
    owner_ref = store.save_text(session_id="session-owner", content="secret", tool_name="t")
    tools = SpillInspectionTools(store)

    with pytest.raises(PermissionDeniedError):
        tools.slice_spill(owner_ref.locator, session_id="session-attacker")
    with pytest.raises(PermissionDeniedError):
        tools.fetch_spill(owner_ref.locator, session_id="session-attacker")
    with pytest.raises(PermissionDeniedError):
        tools.get_info(owner_ref.locator, session_id="session-attacker")


def test_spill_inspection_requires_session_and_uri_locator() -> None:
    store = InMemorySpillStore()
    ref = store.save_text(session_id="s1", content="data", tool_name="t")
    tools = SpillInspectionTools(store)

    with pytest.raises(ValidationError):
        tools.slice_spill(ref.locator, session_id="")
    with pytest.raises(ValidationError):
        tools.slice_spill("C:/elsewhere/file.txt", session_id="s1")


def test_spill_tool_definitions_and_registry() -> None:
    registry = build_spill_tool_registry()

    assert "spill.slice" in registry
    assert "spill.fetch" in registry
    assert "spill.info" in registry

    slice_tool = registry.get("spill.slice")
    assert slice_tool.risk_level == ActionRiskLevel.READ_ONLY
    assert slice_tool.action_class == ActionClass.READ
    assert slice_tool.is_mutation is False
    assert "spill.read" in slice_tool.capabilities
