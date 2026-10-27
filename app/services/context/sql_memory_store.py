"""Durable memory store over the ``memories`` table (the MemoryStore protocol on SQL)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import MemoryType
from app.domain.models.context.memory import MemoryItem
from app.infrastructure.db.models import Memory
from app.services.skills.matching import unaccent_vietnamese

# A personal assistant keeps at most a few hundred items per user; ranking happens in Python.
MAX_SCANNED = 500


class SqlMemoryStore:
    """MemoryStore bound to one database session; writes are flushed, the caller commits.

    Binding to the request session keeps a memory write in the same transaction as the
    rest of the turn (the user row it references, the run record). Search is keyword
    overlap on accent-folded text weighted by importance, which is exact enough for a few
    hundred personal notes and needs no embedding model in the request path.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_memory(self, memory: MemoryItem) -> MemoryItem:
        row = await self._session.get(Memory, memory.id)
        if row is None:
            row = Memory(id=memory.id, user_id=memory.user_id)
            self._session.add(row)
        row.memory_type = memory.memory_type.value
        row.content = memory.content
        row.importance = memory.importance
        row.last_accessed_at = memory.last_accessed_at
        row.is_stale = memory.is_stale
        row.details = dict(memory.metadata)
        await self._session.flush()
        return memory

    async def get_memory(self, memory_id: str) -> MemoryItem | None:
        row = await self._session.get(Memory, memory_id)
        return _to_item(row) if row is not None else None

    async def list_memories(
        self,
        user_id: str,
        memory_type: MemoryType | None = None,
        include_stale: bool = False,
    ) -> list[MemoryItem]:
        stmt = select(Memory).where(Memory.user_id == user_id)
        if memory_type is not None:
            stmt = stmt.where(Memory.memory_type == memory_type.value)
        if not include_stale:
            stmt = stmt.where(Memory.is_stale.is_(False))
        stmt = stmt.order_by(Memory.created_at.desc()).limit(MAX_SCANNED)
        return [_to_item(row) for row in (await self._session.scalars(stmt)).all()]

    async def search_memories(
        self,
        user_id: str,
        query: str,
        memory_type: MemoryType | None = None,
        limit: int = 5,
    ) -> list[MemoryItem]:
        tokens = {token for token in unaccent_vietnamese(query).split() if len(token) > 1}
        scored: list[tuple[float, MemoryItem]] = []
        for item in await self.list_memories(user_id, memory_type=memory_type):
            folded = unaccent_vietnamese(item.content)
            hits = sum(1 for token in tokens if token in folded)
            if tokens and hits == 0:
                continue
            score = (hits / max(len(tokens), 1)) * 0.7 + item.importance * 0.3
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    async def mark_stale(self, memory_id: str) -> bool:
        row = await self._session.get(Memory, memory_id)
        if row is None:
            return False
        row.is_stale = True
        await self._session.flush()
        return True

    async def delete_memory(self, memory_id: str) -> bool:
        row = await self._session.get(Memory, memory_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True


def _to_item(row: Memory) -> MemoryItem:
    details: dict[str, Any] = dict(row.details or {})
    return MemoryItem(
        id=row.id,
        user_id=row.user_id,
        memory_type=MemoryType(row.memory_type),
        content=row.content,
        importance=row.importance,
        last_accessed_at=row.last_accessed_at,
        metadata=details,
        is_stale=row.is_stale,
    )
