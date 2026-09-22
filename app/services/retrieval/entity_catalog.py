"""Dynamic Knowledge Entity Catalog for Multi-Domain RAG.

Discovers, caches, and matches real-world entities (signers, authorities,
document types, project leads) directly from PostgreSQL metadata,
removing the need for hardcoded entity lists in retrieval code.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.infrastructure.db.models import Document
from app.infrastructure.db.session import get_session_factory
from app.services.retrieval.vietnamese_orthography import (
    generate_orthographic_variants,
    normalize_unicode,
)

logger = logging.getLogger(__name__)


@dataclass
class CatalogEntity:
    """An entity discovered dynamically from the document repository."""

    name: str
    category: str  # "signer", "document_type", "authority", "topic"
    variants: list[str] = field(default_factory=list)


class DynamicEntityCatalog:
    """In-memory cached catalog of entities retrieved from PostgreSQL documents.

    Thread-safe and async-compatible with TTL cache invalidation.
    """

    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._last_refresh_time: float = 0.0
        self._entities: dict[str, CatalogEntity] = {}  # lowercase_canonical -> CatalogEntity
        self._lock = asyncio.Lock()

    @property
    def is_expired(self) -> bool:
        """Check if the cache has expired and needs a refresh."""
        return (time.time() - self._last_refresh_time) > self._ttl_seconds

    async def ensure_loaded(self) -> None:
        """Ensure the catalog is populated from DB if empty or expired."""
        if not self._entities or self.is_expired:
            async with self._lock:
                # Double-check inside lock
                if not self._entities or self.is_expired:
                    await self.refresh()

    async def refresh(self) -> None:
        """Fetch all active entities from Document metadata in PostgreSQL."""
        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                docs = (
                    await session.scalars(
                        select(Document).where(Document.is_active == True)  # noqa: E712
                    )
                ).all()

                new_entities: dict[str, CatalogEntity] = {}

                for doc in docs:
                    admin_meta = (doc.metadata_ or {}).get("administrative_metadata", {})
                    if not isinstance(admin_meta, dict):
                        continue

                    # 1. Signers
                    signer = admin_meta.get("signer_name")
                    if signer and isinstance(signer, str) and len(signer.strip()) >= 3:
                        clean_name = normalize_unicode(signer.strip())
                        low = clean_name.lower()
                        if low not in new_entities:
                            vars_ = generate_orthographic_variants(clean_name)
                            new_entities[low] = CatalogEntity(
                                name=clean_name,
                                category="signer",
                                variants=vars_,
                            )

                    # 2. Document Types
                    doc_type = admin_meta.get("document_type")
                    if doc_type and isinstance(doc_type, str) and len(doc_type.strip()) >= 3:
                        clean_type = normalize_unicode(doc_type.strip())
                        low_type = clean_type.lower()
                        if low_type not in new_entities:
                            new_entities[low_type] = CatalogEntity(
                                name=clean_type,
                                category="document_type",
                                variants=[low_type],
                            )

                    # 3. Issuing Authorities
                    auth = admin_meta.get("issuing_authority")
                    if auth and isinstance(auth, str) and len(auth.strip()) >= 3:
                        clean_auth = normalize_unicode(auth.strip())
                        low_auth = clean_auth.lower()
                        if low_auth not in new_entities:
                            new_entities[low_auth] = CatalogEntity(
                                name=clean_auth,
                                category="authority",
                                variants=[low_auth],
                            )

                self._entities = new_entities
                self._last_refresh_time = time.time()
                logger.info(
                    "DynamicEntityCatalog refreshed: %d active entities loaded from %d documents.",
                    len(self._entities),
                    len(docs),
                )
        except Exception as err:
            logger.warning("Failed to refresh DynamicEntityCatalog from DB: %s", err)

    def match_entities(self, query: str) -> list[tuple[str, list[str]]]:
        """Match entities in user query against dynamically loaded catalog.

        Returns:
            list[tuple[str, list[str]]]: List of (canonical_name, list_of_spelling_variants).
        """
        norm_query = normalize_unicode(query).lower()
        matched: list[tuple[str, list[str]]] = []
        matched_keys: set[str] = set()

        for key, entity in self._entities.items():
            # Check if any variant of the entity is contained in query
            for variant in entity.variants:
                if variant in norm_query:
                    if key not in matched_keys:
                        matched_keys.add(key)
                        matched.append((entity.name.lower(), entity.variants))
                    break

        return matched


# Global singleton instance for retrieval services
_global_catalog: DynamicEntityCatalog | None = None


def get_entity_catalog() -> DynamicEntityCatalog:
    """Get the global entity catalog singleton."""
    global _global_catalog
    if _global_catalog is None:
        _global_catalog = DynamicEntityCatalog()
    return _global_catalog


def invalidate_entity_catalog() -> None:
    """Invalidate the entity catalog cache so the next query reloads freshly from DB."""
    global _global_catalog
    if _global_catalog is not None:
        _global_catalog._last_refresh_time = 0.0

