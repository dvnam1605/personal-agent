"""Conversation threads and messages persistence API."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dependencies import get_current_user_id
from app.infrastructure.db.evidence_repository import citation_payload, run_evidence
from app.infrastructure.db.models import AssistantRun, Conversation, Message
from app.infrastructure.db.session import get_db_session

router = APIRouter(prefix="/conversations", tags=["conversations"])


class MessageItem(BaseModel):
    id: str
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageItem] = Field(default_factory=list)


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=255)


class UpdateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1, max_length=255)


# Same bound as QueryRequest.query; a saved question is replayed into follow-up triage.
MAX_USER_TURN_CHARS = 4000
# Generated reports stream into the chat, so answers may be long, but never unbounded.
MAX_ASSISTANT_TURN_CHARS = 200_000


class AppendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(..., max_length=32)
    content: str = Field(..., min_length=1, max_length=MAX_ASSISTANT_TURN_CHARS)
    metadata: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = Field(default=None, max_length=36)


class SaveTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_query: str = Field(..., min_length=1, max_length=MAX_USER_TURN_CHARS)
    assistant_response: str = Field(default="", max_length=MAX_ASSISTANT_TURN_CHARS)
    turn_id: str | None = Field(default=None, max_length=64)
    run_id: str | None = Field(default=None, max_length=36)
    metadata: dict[str, Any] = Field(default_factory=dict)


async def _owned_run_id(session: AsyncSession, user_id: str, run_id: str | None) -> str | None:
    """The client-sent run id, kept only when that run belongs to the caller."""
    if not run_id:
        return None
    run = await session.get(AssistantRun, run_id)
    return run_id if run is not None and run.user_id == user_id else None


async def _server_side_citations(
    session: AsyncSession, user_id: str, body: SaveTurnRequest
) -> dict[str, Any]:
    """Message metadata with citations rebuilt from the run's stored evidence.

    Client-sent citations are only kept when the run recorded no evidence
    (e.g. metadata-route answers) — a client can no longer attach citations
    to evidence the model never saw, and never to another user's run.
    """
    metadata = dict(body.metadata or {})
    if not body.run_id:
        return metadata
    run = await session.get(AssistantRun, body.run_id)
    if run is None or run.user_id != user_id:
        data = dict(metadata.get("data") or {})
        data.pop("citations", None)
        metadata.pop("data", None)
        return {**metadata, "data": data} if data else metadata
    cited = await run_evidence(session, body.run_id, cited_only=True)
    data = dict(metadata.get("data") or {})
    if cited:
        data["citations"] = [citation_payload(row) for row in cited]
    if run.answer_status:
        data["sufficiency"] = run.answer_status
    if data:
        metadata["data"] = data
    return metadata


@router.get(
    "",
    response_model=list[ConversationSummary],
    summary="List all conversations for current user",
)
async def list_conversations(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[ConversationSummary]:
    # Query conversations with message count
    stmt = (
        select(
            Conversation,
            func.count(Message.id).label("msg_count"),
        )
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id)
        .group_by(Conversation.id)
        .order_by(Conversation.updated_at.desc())
    )
    res = await session.execute(stmt)
    rows = res.all()

    results: list[ConversationSummary] = []
    for conv, msg_count in rows:
        results.append(
            ConversationSummary(
                id=conv.id,
                title=conv.title or "Cuộc trò chuyện mới",
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                message_count=msg_count,
            )
        )
    return results


@router.post(
    "",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new conversation session",
)
async def create_conversation(
    body: CreateConversationRequest,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ConversationSummary:
    now = datetime.now(UTC)
    conv = Conversation(
        id=str(uuid.uuid4()),
        user_id=user_id,
        title=body.title.strip() if body.title else "Cuộc trò chuyện mới",
        metadata_={},
        created_at=now,
        updated_at=now,
    )
    session.add(conv)
    await session.commit()
    await session.refresh(conv)

    return ConversationSummary(
        id=conv.id,
        title=conv.title or "Cuộc trò chuyện mới",
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=0,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    summary="Get conversation detail with all messages",
)
async def get_conversation(
    conversation_id: str,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ConversationDetail:
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
    )
    res = await session.execute(stmt)
    conv = res.scalar_one_or_none()
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy cuộc trò chuyện.",
        )

    # Sort messages chronologically
    sorted_messages = sorted(conv.messages, key=lambda m: m.created_at)
    messages_out = [
        MessageItem(
            id=m.id,
            role=m.role,
            content=m.content,
            metadata=m.metadata_ or {},
            created_at=m.created_at,
        )
        for m in sorted_messages
    ]

    return ConversationDetail(
        id=conv.id,
        title=conv.title or "Cuộc trò chuyện mới",
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=messages_out,
    )


@router.patch(
    "/{conversation_id}",
    response_model=ConversationSummary,
    summary="Update conversation title",
)
async def update_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ConversationSummary:
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    )
    res = await session.execute(stmt)
    conv = res.scalar_one_or_none()
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy cuộc trò chuyện.",
        )

    title = body.title.strip()
    conv.title = title
    conv.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(conv)

    return ConversationSummary(
        id=conv.id,
        title=title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete conversation and its messages",
)
async def delete_conversation(
    conversation_id: str,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> None:
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    )
    res = await session.execute(stmt)
    conv = res.scalar_one_or_none()
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy cuộc trò chuyện.",
        )

    # Delete messages first
    del_msg = delete(Message).where(Message.conversation_id == conversation_id)
    await session.execute(del_msg)

    # Delete conversation
    del_conv = delete(Conversation).where(Conversation.id == conversation_id)
    await session.execute(del_conv)

    await session.commit()


@router.post(
    "/{conversation_id}/turn",
    status_code=status.HTTP_200_OK,
    summary="Save a complete Q&A turn into a conversation",
)
async def save_turn(
    conversation_id: str,
    body: SaveTurnRequest,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, str]:
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    )
    res = await session.execute(stmt)
    conv = res.scalar_one_or_none()
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy cuộc trò chuyện.",
        )

    now = datetime.now(UTC)

    # Automatically derive title if conversation title is still default
    if not conv.title or conv.title == "Cuộc trò chuyện mới":
        trimmed = body.user_query.strip()
        conv.title = trimmed[:42] + ("..." if len(trimmed) > 42 else "")

    conv.updated_at = now
    run_id = await _owned_run_id(session, user_id, body.run_id)

    # 1. User message
    user_msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conv.id,
        run_id=run_id,
        role="user",
        content=body.user_query,
        metadata_={"turn_id": body.turn_id} if body.turn_id else {},
        created_at=now,
    )
    session.add(user_msg)

    # 2. Assistant message (if answer provided)
    if body.assistant_response:
        assistant_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            run_id=run_id,
            role="assistant",
            content=body.assistant_response,
            metadata_=await _server_side_citations(session, user_id, body),
            created_at=now,
        )
        session.add(assistant_msg)

    await session.commit()
    return {"status": "saved", "conversation_id": conv.id}
