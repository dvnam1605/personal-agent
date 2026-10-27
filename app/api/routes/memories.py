"""Memory page endpoints: what the assistant remembers about the caller, editable by them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.domain.errors import NotFoundError
from app.domain.models.context.memory import MemoryItem
from app.infrastructure.db.session import get_db_session
from app.services.context.personal_memory import MAX_CONTENT_LEN, PersonalMemoryService
from app.services.context.sql_memory_store import SqlMemoryStore

router = APIRouter(prefix="/memories", tags=["memories"])


class MemoryView(BaseModel):
    """One remembered item as shown on the memory page."""

    id: str
    memory_type: str
    content: str
    category: str | None = None

    @classmethod
    def of(cls, item: MemoryItem) -> MemoryView:
        category = item.metadata.get("category")
        return cls(
            id=item.id,
            memory_type=item.memory_type.value,
            content=item.content,
            category=str(category) if category and not str(category).startswith("note:") else None,
        )


class MemoryWrite(BaseModel):
    """New or edited memory text."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=MAX_CONTENT_LEN)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped


def _service(session: AsyncSession) -> PersonalMemoryService:
    return PersonalMemoryService(SqlMemoryStore(session))


async def _owned(session: AsyncSession, user_id: str, memory_id: str) -> MemoryItem:
    item = await SqlMemoryStore(session).get_memory(memory_id)
    if item is None or item.user_id != user_id or item.is_stale:
        raise NotFoundError("Memory not found.")
    return item


@router.get("", response_model=list[MemoryView])
async def list_memories(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[MemoryView]:
    """Active preferences and facts of the caller, newest first."""
    return [MemoryView.of(item) for item in await _service(session).active(user_id)]


@router.post("", response_model=MemoryView, status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: MemoryWrite,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> MemoryView:
    """Remember something new, exactly as "nhớ rằng …" in chat would."""
    return MemoryView.of(await _service(session).remember(user_id, body.content))


@router.put("/{memory_id}", response_model=MemoryView)
async def update_memory(
    memory_id: str,
    body: MemoryWrite,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> MemoryView:
    """Correct the text of one of the caller's memories."""
    item = await _owned(session, user_id, memory_id)
    updated = item.model_copy(update={"content": body.content})
    return MemoryView.of(await SqlMemoryStore(session).save_memory(updated))


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: str,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> None:
    """Delete one of the caller's memories permanently."""
    await _owned(session, user_id, memory_id)
    await SqlMemoryStore(session).delete_memory(memory_id)
