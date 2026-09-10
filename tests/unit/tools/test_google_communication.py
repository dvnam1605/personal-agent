"""Unit tests for the P6 communication tool wrapper and registry classifications."""

import asyncio
from typing import Any

import pytest

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.errors import NotFoundError
from app.domain.models import ToolContext, ToolInput
from app.services.approvals import generate_approval_token
from app.services.communication import CommunicationService
from app.tools import GoogleCommunicationTools, build_communication_tool_registry
from app.tools.google_communication import COMMUNICATION_TOOL_DEFINITIONS


class _RetryCounter:
    last_retry_count = 0


class StubCommunicationService:
    """Duck-typed CommunicationService that records dispatch calls; no network."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.gmail = _RetryCounter()
        self.contacts = _RetryCounter()

    def _record(self, name: str, **kwargs: Any) -> str:
        self.calls.append((name, kwargs))
        return f"ok:{name}"

    async def search_messages(self, *args: Any, **kwargs: Any):
        return self._record("search_messages", args=args, **kwargs)

    async def get_message(self, message_id: str, *, format: str = "full"):
        return self._record("get_message", message_id=message_id, format=format)

    async def get_thread(self, thread_id: str, *, format: str = "full"):
        return self._record("get_thread", thread_id=thread_id, format=format)

    async def list_threads(self, *args: Any, **kwargs: Any):
        return self._record("list_threads", args=args, **kwargs)

    async def create_draft(self, *args: Any, **kwargs: Any):
        return self._record("create_draft", args=args, **kwargs)

    async def update_draft(self, *args: Any, **kwargs: Any):
        return self._record("update_draft", args=args, **kwargs)

    async def delete_draft(self, draft_id: str):
        return self._record("delete_draft", draft_id=draft_id)

    async def send_draft(self, draft_id: str):
        return self._record("send_draft", draft_id=draft_id)

    async def reply(self, message_id: str, body_text: str, **kwargs: Any):
        return self._record("reply", message_id=message_id, body_text=body_text, **kwargs)

    async def forward(self, message_id: str, to: Any, body_text: str, **kwargs: Any):
        return self._record("forward", message_id=message_id, to=to, body_text=body_text, **kwargs)

    async def archive(self, message_id: str):
        return self._record("archive", message_id=message_id)

    async def trash(self, message_id: str):
        return self._record("trash", message_id=message_id)

    async def add_label(self, message_id: str, label_ids: Any):
        return self._record("add_label", message_id=message_id, label_ids=label_ids)

    async def remove_label(self, message_id: str, label_ids: Any):
        return self._record("remove_label", message_id=message_id, label_ids=label_ids)

    async def search_contacts(self, query: str, **kwargs: Any):
        return self._record("search_contacts", query=query, **kwargs)

    async def get_contact(self, resource_name: str):
        return self._record("get_contact", resource_name=resource_name)

    async def resolve_person(self, query: str, **kwargs: Any):
        return self._record("resolve_person", query=query, **kwargs)


def _tools() -> tuple[GoogleCommunicationTools, StubCommunicationService]:
    service = StubCommunicationService()
    return GoogleCommunicationTools(service), service  # type: ignore[arg-type]


def _context(read_only_view: bool = False, approval_token: str | None = None) -> ToolContext:
    return ToolContext(
        run_id="run-1",
        user_id="user-1",
        read_only_view=read_only_view,
        approval_token=approval_token,
    )


def test_communication_definitions_are_complete_and_classified() -> None:
    names = [definition.name for definition in COMMUNICATION_TOOL_DEFINITIONS]
    assert len(names) == len(set(names)) == 17

    gmail_reads = {
        "gmail.search_messages",
        "gmail.get_message",
        "gmail.get_thread",
        "gmail.list_threads",
    }
    assert gmail_reads.issubset(set(names))
    assert "contacts.resolve_person" in names

    by_name = {d.name: d for d in COMMUNICATION_TOOL_DEFINITIONS}
    for read_tool in by_name.values():
        if not read_tool.is_mutation:
            assert read_tool.action_class == ActionClass.READ
            assert read_tool.risk_level == ActionRiskLevel.READ_ONLY

    send_draft = by_name["gmail.send_draft"]
    assert send_draft.action_class == ActionClass.EXTERNAL_COMMUNICATION
    assert send_draft.risk_level == ActionRiskLevel.HIGH_IMPACT_WRITE
    assert send_draft.is_mutation is True

    trash = by_name["gmail.trash"]
    assert trash.action_class == ActionClass.DESTRUCTIVE
    assert trash.risk_level == ActionRiskLevel.IRREVERSIBLE

    archive = by_name["gmail.archive"]
    assert archive.action_class == ActionClass.SAFE_WRITE
    assert archive.risk_level == ActionRiskLevel.LOW_IMPACT_WRITE


def test_build_communication_registry_filters_read_only() -> None:
    registry = build_communication_tool_registry()
    all_tools = registry.list()
    assert len(all_tools) == 17

    mutations = [tool.name for tool in all_tools if tool.is_mutation]
    reads = [tool.name for tool in all_tools if not tool.is_mutation]
    assert len(mutations) == 10
    assert len(reads) == 7

    read_only_view = registry.as_read_only()
    assert read_only_view.is_read_only
    assert set(read_only_view.tool_names) == set(reads)
    with pytest.raises(NotFoundError):
        read_only_view.get("gmail.send_draft")


@pytest.mark.asyncio
async def test_execute_dispatches_every_declared_tool() -> None:
    tools, service = _tools()

    cases: list[tuple[str, dict[str, Any], str]] = [
        ("gmail.search_messages", {"query": "rag"}, "search_messages"),
        ("gmail.get_message", {"message_id": "m1"}, "get_message"),
        ("gmail.get_thread", {"thread_id": "t1"}, "get_thread"),
        ("gmail.list_threads", {"query": "inbox"}, "list_threads"),
        (
            "gmail.create_draft",
            {"to": ["a@example.com"], "subject": "Hi", "body_text": "Body"},
            "create_draft",
        ),
        (
            "gmail.update_draft",
            {"draft_id": "d1", "to": ["a@example.com"]},
            "update_draft",
        ),
        ("gmail.delete_draft", {"draft_id": "d1"}, "delete_draft"),
        ("gmail.send_draft", {"draft_id": "d1"}, "send_draft"),
        ("gmail.reply", {"message_id": "m1", "body_text": "Reply"}, "reply"),
        (
            "gmail.forward",
            {"message_id": "m1", "to": ["b@example.com"], "body_text": "Fwd"},
            "forward",
        ),
        ("gmail.archive", {"message_id": "m1"}, "archive"),
        ("gmail.trash", {"message_id": "m1"}, "trash"),
        ("gmail.add_label", {"message_id": "m1", "label_ids": ["STARRED"]}, "add_label"),
        (
            "gmail.remove_label",
            {"message_id": "m1", "label_ids": ["STARRED"]},
            "remove_label",
        ),
        ("contacts.search", {"query": "Nam"}, "search_contacts"),
        ("contacts.get", {"resource_name": "people/1"}, "get_contact"),
        ("contacts.resolve_person", {"query": "Nam"}, "resolve_person"),
    ]

    for tool_name, arguments, expected_call in cases:
        definition = next(d for d in COMMUNICATION_TOOL_DEFINITIONS if d.name == tool_name)
        approval = None
        if definition.is_mutation:
            approval = generate_approval_token(
                approval_id=f"comm-{tool_name}",
                tool_name=tool_name,
                run_id="run-1",
                arguments=dict(arguments),
            )
        result = await tools.execute(
            ToolInput(tool_name=tool_name, arguments=dict(arguments)),
            _context(approval_token=approval),
        )
        assert result.success is True, f"{tool_name} failed: {result.error}"
        assert result.output == f"ok:{expected_call}"
        assert result.metadata.tool_name == tool_name

    dispatched = {name for name, _ in service.calls}
    assert len(dispatched) == len(cases)


@pytest.mark.asyncio
async def test_communication_mutation_fails_without_approval_token() -> None:
    tools, _ = _tools()
    result = await tools.execute(
        ToolInput(tool_name="gmail.send_draft", arguments={"draft_id": "d1"}),
        _context(),
    )
    assert result.success is False
    assert "requires human approval verification" in (result.error or "")


@pytest.mark.asyncio
async def test_communication_mutation_fails_with_spoofed_or_expired_token() -> None:
    tools, _ = _tools()
    # 1. Arbitrary spoofed token fails closed (H1)
    res_spoofed = await tools.execute(
        ToolInput(tool_name="gmail.send_draft", arguments={"draft_id": "d1"}),
        _context(approval_token="test-approved"),
    )
    assert res_spoofed.success is False
    assert "rejected: approval token is invalid, expired, or unverified" in (
        res_spoofed.error or ""
    )

    # 2. Token for wrong tool fails closed
    wrong_tok = generate_approval_token(approval_id="c-wrong", tool_name="gmail.delete_draft")
    res_wrong = await tools.execute(
        ToolInput(tool_name="gmail.send_draft", arguments={"draft_id": "d1"}),
        _context(approval_token=wrong_tok),
    )
    assert res_wrong.success is False
    assert "rejected: approval token is invalid, expired, or unverified" in (res_wrong.error or "")


@pytest.mark.asyncio
async def test_unregistered_tool_fails_without_dispatch() -> None:
    tools, service = _tools()
    result = await tools.execute(ToolInput(tool_name="gmail.unknown", arguments={}), _context())
    assert result.success is False
    assert "not registered" in (result.error or "")
    assert not service.calls


@pytest.mark.asyncio
async def test_unexpected_exception_is_wrapped_as_failure() -> None:
    class ExplodingService(StubCommunicationService):
        async def get_message(self, message_id: str, *, format: str = "full"):
            raise RuntimeError("boom")

    tools = GoogleCommunicationTools(ExplodingService())  # type: ignore[arg-type]
    result = await tools.execute(
        ToolInput(tool_name="gmail.get_message", arguments={"message_id": "m1"}), _context()
    )
    assert result.success is False
    assert result.error
    assert result.output is None


def test_service_resolve_person_exact_and_bounds() -> None:
    """resolve_person stays deterministic and validates pagination bounds."""
    from app.domain.models import Contact, ContactPage

    contact = Contact(resource_name="people/1", display_name="Nam", emails=[], phones=[])

    class StubContacts:
        async def search(self, query: str, *, page_size: int = 30, page_token=None):
            assert 1 <= page_size <= 30
            return ContactPage(items=[contact], next_page_token=None)

    service = CommunicationService.__new__(CommunicationService)
    service.gmail = None  # type: ignore[assignment]
    service.contacts = StubContacts()  # type: ignore[assignment]

    resolved = asyncio.run(service.resolve_person("nam"))
    assert resolved.status.value == "exact"
    assert resolved.contact is not None and resolved.contact.resource_name == "people/1"


@pytest.mark.asyncio
async def test_service_resolve_person_rejects_invalid_bounds() -> None:
    from app.domain.errors import ValidationError as DomainValidationError

    service = CommunicationService.__new__(CommunicationService)
    service.gmail = None  # type: ignore[assignment]
    service.contacts = None  # type: ignore[assignment]

    with pytest.raises(DomainValidationError):
        await service.resolve_person("nam", max_pages=0)
    with pytest.raises(DomainValidationError):
        await service.resolve_person("")
    with pytest.raises(DomainValidationError):
        await service.resolve_person("   ")
