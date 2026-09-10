"""Preference memory service managing stable user preferences and superseding stale items (spec P17-03)."""

from __future__ import annotations

from app.domain.enums import MemoryType
from app.domain.models.context.memory import MemoryItem
from app.services.context.memory_store import MemoryStore


class PreferenceService:
    """Manages long-term stable user preferences with conflict resolution and staleness rejection."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def record_preference(
        self,
        user_id: str,
        content: str,
        category: str = "general",
        importance: float = 0.8,
    ) -> MemoryItem:
        """Record a new preference, marking any conflicting existing preference in same category stale."""
        # Find existing preferences in same category
        existing = await self._store.list_memories(
            user_id, memory_type=MemoryType.PREFERENCE, include_stale=False
        )
        for old in existing:
            if old.metadata.get("category") == category:
                if old.content.strip().lower() == content.strip().lower():
                    # Identical preference already active; return it without churn (L3)
                    return old
                # Mark conflicting prior preference as stale
                await self._store.mark_stale(old.id)

        item = MemoryItem(
            user_id=user_id,
            memory_type=MemoryType.PREFERENCE,
            content=content,
            importance=importance,
            metadata={"category": category},
            is_stale=False,
        )
        return await self._store.save_memory(item)

    async def get_active_preferences(
        self, user_id: str, category: str | None = None
    ) -> list[MemoryItem]:
        """Retrieve only active, non-stale user preferences."""
        prefs = await self._store.list_memories(
            user_id, memory_type=MemoryType.PREFERENCE, include_stale=False
        )
        if category is not None:
            return [p for p in prefs if p.metadata.get("category") == category]
        return prefs

    async def reject_stale_preference(self, user_id: str, preference_id: str) -> bool:
        """Explicitly invalidate or reject an outdated preference."""
        item = await self._store.get_memory(preference_id)
        if item is not None and item.user_id == user_id:
            return await self._store.mark_stale(preference_id)
        return False
