"""Memory storage abstraction and in-memory implementation (spec P17-03..P17-05)."""

from __future__ import annotations

from typing import Protocol

from app.domain.enums import MemoryType
from app.domain.models.memory import MemoryItem


class MemoryStore(Protocol):
    """Protocol for memory persistence and retrieval."""

    async def save_memory(self, memory: MemoryItem) -> MemoryItem:
        """Persist or update a memory item."""
        ...

    async def get_memory(self, memory_id: str) -> MemoryItem | None:
        """Fetch memory item by ID."""
        ...

    async def list_memories(
        self,
        user_id: str,
        memory_type: MemoryType | None = None,
        include_stale: bool = False,
    ) -> list[MemoryItem]:
        """List memories for user, optionally filtered by type and staleness."""
        ...

    async def search_memories(
        self,
        user_id: str,
        query: str,
        memory_type: MemoryType | None = None,
        limit: int = 5,
    ) -> list[MemoryItem]:
        """Search active memories by semantic or keyword relevance."""
        ...

    async def mark_stale(self, memory_id: str) -> bool:
        """Mark a memory item as superseded / stale."""
        ...

    async def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory item."""
        ...


class InMemoryMemoryStore:
    """In-memory store for personal memories in single-process testing and development (PostgreSQL/pgvector persistence planned for P18)."""

    def __init__(self) -> None:
        self._memories: dict[str, MemoryItem] = {}

    async def save_memory(self, memory: MemoryItem) -> MemoryItem:
        self._memories[memory.id] = memory
        return memory

    async def get_memory(self, memory_id: str) -> MemoryItem | None:
        return self._memories.get(memory_id)

    async def list_memories(
        self,
        user_id: str,
        memory_type: MemoryType | None = None,
        include_stale: bool = False,
    ) -> list[MemoryItem]:
        results: list[MemoryItem] = []
        for m in self._memories.values():
            if m.user_id != user_id:
                continue
            if not include_stale and m.is_stale:
                continue
            if memory_type is not None and m.memory_type != memory_type:
                continue
            results.append(m)
        return results

    async def search_memories(
        self,
        user_id: str,
        query: str,
        memory_type: MemoryType | None = None,
        limit: int = 5,
    ) -> list[MemoryItem]:
        q_tokens = set(query.lower().split())
        candidates = await self.list_memories(user_id, memory_type=memory_type, include_stale=False)

        scored: list[tuple[float, MemoryItem]] = []
        for item in candidates:
            content_lower = item.content.lower()
            # Simple keyword overlap + importance weighting for deterministic non-embedding search
            matches = sum(1 for tok in q_tokens if tok in content_lower)
            score = (matches / max(len(q_tokens), 1)) * 0.7 + (item.importance * 0.3)
            if matches > 0 or not q_tokens:
                scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

    async def mark_stale(self, memory_id: str) -> bool:
        m = self._memories.get(memory_id)
        if m is not None:
            self._memories[memory_id] = m.model_copy(update={"is_stale": True})
            return True
        return False

    async def delete_memory(self, memory_id: str) -> bool:
        if memory_id in self._memories:
            del self._memories[memory_id]
            return True
        return False
