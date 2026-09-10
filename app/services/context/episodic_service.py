"""Episodic memory service storing confirmed meaningful events, decisions, and milestones (spec P17-04)."""

from __future__ import annotations

import re

from app.domain.enums import MemoryType
from app.domain.models.memory import MemoryItem
from app.services.context.memory_store import MemoryStore

_TRIVIAL_CHATTER_PATTERNS = [
    re.compile(
        r"^(xin chào|chào(\s+(bạn|anh|chị|mọi người|nhé|nha|ạ))*|hi|hello|hey|cảm ơn(\s+(bạn|anh|chị|nhiều|nhé|nha|ạ))*|thank you|thanks|ok|ừ|dạ|vâng)[.!]?$",
        re.IGNORECASE,
    ),
    re.compile(r"^\d+\s*[\+\-\*\/]\s*\d+$"),  # Pure math
]


class EpisodicMemoryService:
    """Manages long-term episodic memory for milestone events and confirmed decisions."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def record_event(
        self,
        user_id: str,
        content: str,
        importance: float = 0.7,
        event_time: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> MemoryItem | None:
        """Store meaningful confirmed events, rejecting ephemeral conversational turns."""
        cleaned = content.strip()
        if not cleaned or self.is_ephemeral_chatter(cleaned):
            return None

        meta = dict(metadata or {})
        if event_time:
            meta["event_time"] = event_time

        item = MemoryItem(
            user_id=user_id,
            memory_type=MemoryType.EPISODIC,
            content=cleaned,
            importance=importance,
            metadata=meta,
            is_stale=False,
        )
        return await self._store.save_memory(item)

    async def retrieve_relevant_episodes(
        self, user_id: str, query: str, limit: int = 5
    ) -> list[MemoryItem]:
        """Retrieve active episodic memories matching the query."""
        return await self._store.search_memories(
            user_id, query=query, memory_type=MemoryType.EPISODIC, limit=limit
        )

    @staticmethod
    def is_ephemeral_chatter(text: str) -> bool:
        """True if the text is trivial conversational pleasantry or raw noise."""
        text_strip = text.strip()
        if len(text_strip) < 4:
            return True
        for pattern in _TRIVIAL_CHATTER_PATTERNS:
            if pattern.match(text_strip):
                return True
        return False
