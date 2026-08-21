"""Deterministic Gmail/Contacts tool declarations and execution wrappers."""

import time
from typing import TYPE_CHECKING, Any

from app.domain.enums import ActionClass, ActionRiskLevel
from app.domain.errors import AppError, PermissionDeniedError, ValidationError
from app.domain.models import (
    ToolContext,
    ToolDefinition,
    ToolExecutionMetadata,
    ToolInput,
    ToolResult,
)
from app.integrations.google_contacts import CONTACTS_READONLY_SCOPE
from app.integrations.google_gmail import GMAIL_MODIFY_SCOPE
from app.services.communication import CommunicationService
from app.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _tool(
    name: str,
    description: str,
    *,
    category: str,
    capabilities: list[str],
    parameters_schema: dict[str, Any],
    required_scopes: list[str],
    action_class: ActionClass = ActionClass.READ,
    risk_level: ActionRiskLevel = ActionRiskLevel.READ_ONLY,
    is_mutation: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        category=category,
        capabilities=capabilities,
        parameters_schema=parameters_schema,
        required_scopes=required_scopes,
        action_class=action_class,
        risk_level=risk_level,
        is_mutation=is_mutation,
    )


_GMAIL_READ = [GMAIL_MODIFY_SCOPE]
_CONTACTS_READ = [CONTACTS_READONLY_SCOPE]
_STRING = {"type": "string"}


COMMUNICATION_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    _tool(
        "gmail.search_messages",
        "Search Gmail messages with deterministic provider query and pagination.",
        category="gmail",
        capabilities=["gmail.read", "gmail.search"],
        parameters_schema={
            "type": "object",
            "properties": {
                "query": _STRING,
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
                "page_token": _STRING,
                "include_spam_trash": {"type": "boolean"},
            },
        },
        required_scopes=_GMAIL_READ,
    ),
    _tool(
        "gmail.get_message",
        "Fetch one normalized Gmail message.",
        category="gmail",
        capabilities=["gmail.read"],
        parameters_schema={
            "type": "object",
            "properties": {"message_id": _STRING, "format": _STRING},
            "required": ["message_id"],
        },
        required_scopes=_GMAIL_READ,
    ),
    _tool(
        "gmail.get_thread",
        "Fetch one normalized Gmail thread and its messages.",
        category="gmail",
        capabilities=["gmail.read", "gmail.threads"],
        parameters_schema={
            "type": "object",
            "properties": {"thread_id": _STRING, "format": _STRING},
            "required": ["thread_id"],
        },
        required_scopes=_GMAIL_READ,
    ),
    _tool(
        "gmail.list_threads",
        "List normalized Gmail thread references with pagination.",
        category="gmail",
        capabilities=["gmail.read", "gmail.threads"],
        parameters_schema={
            "type": "object",
            "properties": {
                "query": _STRING,
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
                "page_token": _STRING,
                "include_spam_trash": {"type": "boolean"},
            },
        },
        required_scopes=_GMAIL_READ,
    ),
    _tool(
        "gmail.create_draft",
        "Create a Gmail draft without sending it.",
        category="gmail",
        capabilities=["gmail.drafts", "gmail.write"],
        parameters_schema={
            "type": "object",
            "properties": {
                "to": {"type": "array"},
                "subject": _STRING,
                "body_text": _STRING,
                "cc": {"type": "array"},
                "bcc": {"type": "array"},
                "body_html": _STRING,
                "thread_id": _STRING,
            },
            "required": ["to"],
        },
        required_scopes=_GMAIL_READ,
        action_class=ActionClass.SAFE_WRITE,
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "gmail.update_draft",
        "Replace the contents of an existing Gmail draft.",
        category="gmail",
        capabilities=["gmail.drafts", "gmail.write"],
        parameters_schema={
            "type": "object",
            "properties": {
                "draft_id": _STRING,
                "to": {"type": "array"},
                "subject": _STRING,
                "body_text": _STRING,
                "cc": {"type": "array"},
                "bcc": {"type": "array"},
                "body_html": _STRING,
                "thread_id": _STRING,
            },
            "required": ["draft_id", "to"],
        },
        required_scopes=_GMAIL_READ,
        action_class=ActionClass.SAFE_WRITE,
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "gmail.delete_draft",
        "Permanently delete a Gmail draft.",
        category="gmail",
        capabilities=["gmail.drafts", "gmail.delete"],
        parameters_schema={
            "type": "object",
            "properties": {"draft_id": _STRING},
            "required": ["draft_id"],
        },
        required_scopes=_GMAIL_READ,
        action_class=ActionClass.DESTRUCTIVE,
        risk_level=ActionRiskLevel.IRREVERSIBLE,
        is_mutation=True,
    ),
    _tool(
        "gmail.send_draft",
        "Send an existing Gmail draft as external communication.",
        category="gmail",
        capabilities=["gmail.send", "gmail.write"],
        parameters_schema={
            "type": "object",
            "properties": {"draft_id": _STRING},
            "required": ["draft_id"],
        },
        required_scopes=_GMAIL_READ,
        action_class=ActionClass.EXTERNAL_COMMUNICATION,
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "gmail.reply",
        "Send a deterministic reply in an existing Gmail thread.",
        category="gmail",
        capabilities=["gmail.send", "gmail.write"],
        parameters_schema={
            "type": "object",
            "properties": {
                "message_id": _STRING,
                "body_text": _STRING,
                "cc": {"type": "array"},
                "bcc": {"type": "array"},
                "body_html": _STRING,
            },
            "required": ["message_id", "body_text"],
        },
        required_scopes=_GMAIL_READ,
        action_class=ActionClass.EXTERNAL_COMMUNICATION,
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "gmail.forward",
        "Send a deterministic forward of an existing Gmail message.",
        category="gmail",
        capabilities=["gmail.send", "gmail.write"],
        parameters_schema={
            "type": "object",
            "properties": {
                "message_id": _STRING,
                "to": {"type": "array"},
                "body_text": _STRING,
                "cc": {"type": "array"},
                "bcc": {"type": "array"},
                "body_html": _STRING,
            },
            "required": ["message_id", "to", "body_text"],
        },
        required_scopes=_GMAIL_READ,
        action_class=ActionClass.EXTERNAL_COMMUNICATION,
        risk_level=ActionRiskLevel.HIGH_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "gmail.archive",
        "Remove the Inbox label from a Gmail message.",
        category="gmail",
        capabilities=["gmail.modify", "gmail.labels"],
        parameters_schema={
            "type": "object",
            "properties": {"message_id": _STRING},
            "required": ["message_id"],
        },
        required_scopes=_GMAIL_READ,
        action_class=ActionClass.SAFE_WRITE,
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "gmail.trash",
        "Move a Gmail message to trash.",
        category="gmail",
        capabilities=["gmail.modify", "gmail.delete"],
        parameters_schema={
            "type": "object",
            "properties": {"message_id": _STRING},
            "required": ["message_id"],
        },
        required_scopes=_GMAIL_READ,
        action_class=ActionClass.DESTRUCTIVE,
        risk_level=ActionRiskLevel.IRREVERSIBLE,
        is_mutation=True,
    ),
    _tool(
        "gmail.add_label",
        "Add one or more labels to a Gmail message.",
        category="gmail",
        capabilities=["gmail.modify", "gmail.labels"],
        parameters_schema={
            "type": "object",
            "properties": {"message_id": _STRING, "label_ids": {"type": "array"}},
            "required": ["message_id", "label_ids"],
        },
        required_scopes=_GMAIL_READ,
        action_class=ActionClass.SAFE_WRITE,
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "gmail.remove_label",
        "Remove one or more labels from a Gmail message.",
        category="gmail",
        capabilities=["gmail.modify", "gmail.labels"],
        parameters_schema={
            "type": "object",
            "properties": {"message_id": _STRING, "label_ids": {"type": "array"}},
            "required": ["message_id", "label_ids"],
        },
        required_scopes=_GMAIL_READ,
        action_class=ActionClass.SAFE_WRITE,
        risk_level=ActionRiskLevel.LOW_IMPACT_WRITE,
        is_mutation=True,
    ),
    _tool(
        "contacts.search",
        "Search Google Contacts with deterministic pagination.",
        category="contacts",
        capabilities=["contacts.read", "contacts.search"],
        parameters_schema={
            "type": "object",
            "properties": {
                "query": _STRING,
                "page_size": {"type": "integer", "minimum": 1, "maximum": 30},
                "page_token": _STRING,
            },
            "required": ["query"],
        },
        required_scopes=_CONTACTS_READ,
    ),
    _tool(
        "contacts.get",
        "Fetch one normalized Google Contact.",
        category="contacts",
        capabilities=["contacts.read"],
        parameters_schema={
            "type": "object",
            "properties": {"resource_name": _STRING},
            "required": ["resource_name"],
        },
        required_scopes=_CONTACTS_READ,
    ),
    _tool(
        "contacts.resolve_person",
        "Resolve a person by exact deterministic email/name matching; never guess among candidates.",
        category="contacts",
        capabilities=["contacts.read", "contacts.resolve"],
        parameters_schema={
            "type": "object",
            "properties": {
                "query": _STRING,
                "page_size": {"type": "integer", "minimum": 1, "maximum": 30},
                "max_pages": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
        required_scopes=_CONTACTS_READ,
    ),
)

_DEFINITIONS_BY_NAME = {
    definition.name: definition for definition in COMMUNICATION_TOOL_DEFINITIONS
}


def communication_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Return defensive copies of every P6 communication tool declaration."""
    return tuple(definition.model_copy(deep=True) for definition in COMMUNICATION_TOOL_DEFINITIONS)


def build_communication_tool_registry() -> ToolRegistry:
    """Build a registry containing only Gmail and Contacts tools."""
    return ToolRegistry(communication_tool_definitions())


class GoogleCommunicationTools:
    """Tool wrapper that exposes typed deterministic service calls as ToolResult."""

    def __init__(self, service: CommunicationService) -> None:
        self.service = service

    @classmethod
    async def for_user(
        cls,
        oauth_service,
        session: "AsyncSession",
        user_id: str,
        **kwargs: Any,
    ) -> "GoogleCommunicationTools":
        service = await CommunicationService.for_user(oauth_service, session, user_id, **kwargs)
        return cls(service)

    async def execute(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        """Execute one declared tool without introducing an LLM decision point."""
        definition = _DEFINITIONS_BY_NAME.get(tool_input.tool_name)
        if definition is None:
            return self._failure(tool_input.tool_name, "Communication tool is not registered.")
        started = time.perf_counter()
        try:
            if context.read_only_view and definition.is_mutation:
                raise PermissionDeniedError("Read-only tool views cannot execute mutations.")
            output = await self._dispatch(tool_input.tool_name, tool_input.arguments)
            return ToolResult(
                tool_name=tool_input.tool_name,
                success=True,
                output=output,
                metadata=self._metadata(tool_input.tool_name, started),
            )
        except AppError as exc:
            return self._failure(
                tool_input.tool_name,
                exc.message,
                started=started,
            )
        except Exception:
            return self._failure(
                tool_input.tool_name,
                "Communication tool execution failed.",
                started=started,
            )

    async def invoke(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        """Alias used by generic tool runtimes."""
        return await self.execute(tool_input, context)

    async def _dispatch(self, name: str, args: dict[str, Any]) -> Any:
        if name == "gmail.search_messages":
            return await self.service.search_messages(
                str(args.get("query") or ""),
                page_size=int(args.get("page_size", 100)),
                page_token=args.get("page_token"),
                include_spam_trash=bool(args.get("include_spam_trash", False)),
            )
        if name == "gmail.get_message":
            return await self.service.get_message(
                args.get("message_id", ""), format=args.get("format", "full")
            )
        if name == "gmail.get_thread":
            return await self.service.get_thread(
                args.get("thread_id", ""), format=args.get("format", "full")
            )
        if name == "gmail.list_threads":
            return await self.service.list_threads(
                str(args.get("query") or ""),
                page_size=int(args.get("page_size", 100)),
                page_token=args.get("page_token"),
                include_spam_trash=bool(args.get("include_spam_trash", False)),
            )
        if name == "gmail.create_draft":
            return await self.service.create_draft(
                args.get("to", []),
                str(args.get("subject") or ""),
                str(args.get("body_text") or ""),
                cc=args.get("cc", []),
                bcc=args.get("bcc", []),
                body_html=args.get("body_html"),
                thread_id=args.get("thread_id"),
            )
        if name == "gmail.update_draft":
            return await self.service.update_draft(
                args.get("draft_id", ""),
                args.get("to", []),
                str(args.get("subject") or ""),
                str(args.get("body_text") or ""),
                cc=args.get("cc", []),
                bcc=args.get("bcc", []),
                body_html=args.get("body_html"),
                thread_id=args.get("thread_id"),
            )
        if name == "gmail.delete_draft":
            return await self.service.delete_draft(args.get("draft_id", ""))
        if name == "gmail.send_draft":
            return await self.service.send_draft(args.get("draft_id", ""))
        if name == "gmail.reply":
            return await self.service.reply(
                args.get("message_id", ""),
                str(args.get("body_text") or ""),
                cc=args.get("cc", []),
                bcc=args.get("bcc", []),
                body_html=args.get("body_html"),
            )
        if name == "gmail.forward":
            return await self.service.forward(
                args.get("message_id", ""),
                args.get("to", []),
                str(args.get("body_text") or ""),
                cc=args.get("cc", []),
                bcc=args.get("bcc", []),
                body_html=args.get("body_html"),
            )
        if name == "gmail.archive":
            return await self.service.archive(args.get("message_id", ""))
        if name == "gmail.trash":
            return await self.service.trash(args.get("message_id", ""))
        if name == "gmail.add_label":
            return await self.service.add_label(
                args.get("message_id", ""), args.get("label_ids", [])
            )
        if name == "gmail.remove_label":
            return await self.service.remove_label(
                args.get("message_id", ""), args.get("label_ids", [])
            )
        if name == "contacts.search":
            return await self.service.search_contacts(
                args.get("query", ""),
                page_size=int(args.get("page_size", 30)),
                page_token=args.get("page_token"),
            )
        if name == "contacts.get":
            return await self.service.get_contact(args.get("resource_name", ""))
        if name == "contacts.resolve_person":
            return await self.service.resolve_person(
                args.get("query", ""),
                page_size=int(args.get("page_size", 30)),
                max_pages=int(args.get("max_pages", 8)),
            )
        raise ValidationError("Communication tool is not registered.")

    def _metadata(self, tool_name: str, started: float) -> ToolExecutionMetadata:
        if tool_name.startswith("gmail."):
            retry_count = self.service.gmail.last_retry_count
        elif tool_name.startswith("contacts."):
            retry_count = self.service.contacts.last_retry_count
        else:
            retry_count = 0
        return ToolExecutionMetadata(
            tool_name=tool_name,
            latency_ms=(time.perf_counter() - started) * 1000,
            retry_count=retry_count,
        )

    def _failure(
        self,
        tool_name: str,
        message: str,
        *,
        started: float | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            error=message,
            metadata=self._metadata(tool_name, started or time.perf_counter()),
        )


__all__ = [
    "COMMUNICATION_TOOL_DEFINITIONS",
    "GoogleCommunicationTools",
    "build_communication_tool_registry",
    "communication_tool_definitions",
]
