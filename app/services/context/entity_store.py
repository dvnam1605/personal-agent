"""Entity storage abstractions and in-memory implementation (spec P17-01)."""

from __future__ import annotations

from typing import Protocol

from app.domain.enums import EntityType
from app.domain.models.context.entity import EntityRecord


class EntityStore(Protocol):
    """Protocol for entity record persistence."""

    async def save_entity(self, entity: EntityRecord) -> EntityRecord:
        """Persist or update an entity record."""
        ...

    async def get_entity(self, entity_id: str) -> EntityRecord | None:
        """Fetch an entity record by unique ID."""
        ...

    async def list_entities(
        self, user_id: str, entity_type: EntityType | None = None
    ) -> list[EntityRecord]:
        """List entities for a user, optionally filtered by entity type."""
        ...

    async def find_by_name_or_alias(
        self, user_id: str, name_or_alias: str, entity_type: EntityType | None = None
    ) -> list[EntityRecord]:
        """Find entity records matching canonical name or alias (case-insensitive)."""
        ...

    async def delete_entity(self, entity_id: str) -> bool:
        """Delete an entity by ID."""
        ...


class InMemoryEntityStore:
    """In-memory entity store for single-process testing and development (PostgreSQL/pgvector persistence planned for P18)."""

    def __init__(self) -> None:
        self._entities: dict[str, EntityRecord] = {}

    async def save_entity(self, entity: EntityRecord) -> EntityRecord:
        self._entities[entity.id] = entity
        return entity

    async def get_entity(self, entity_id: str) -> EntityRecord | None:
        return self._entities.get(entity_id)

    async def list_entities(
        self, user_id: str, entity_type: EntityType | None = None
    ) -> list[EntityRecord]:
        results: list[EntityRecord] = []
        for e in self._entities.values():
            if e.user_id == user_id:
                if entity_type is None or e.entity_type == entity_type:
                    results.append(e)
        return results

    async def find_by_name_or_alias(
        self, user_id: str, name_or_alias: str, entity_type: EntityType | None = None
    ) -> list[EntityRecord]:
        target = name_or_alias.strip().lower()
        if not target:
            return []
        matches: list[EntityRecord] = []
        for e in self._entities.values():
            if e.user_id != user_id:
                continue
            if entity_type is not None and e.entity_type != entity_type:
                continue
            canonical_lower = e.canonical_name.lower()
            aliases_lower = [a.lower() for a in e.aliases]
            if target == canonical_lower or target in aliases_lower:
                matches.append(e)
            elif target in canonical_lower or any(target in a for a in aliases_lower):
                # Substring match if exact match not yet found
                matches.append(e)
        return matches

    async def delete_entity(self, entity_id: str) -> bool:
        if entity_id in self._entities:
            del self._entities[entity_id]
            return True
        return False
