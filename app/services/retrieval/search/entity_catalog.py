"""Dynamic Knowledge Entity Catalog for Multi-Domain RAG.

Discovers, caches, and matches real-world entities (signers, authorities,
document types, project leads) directly from PostgreSQL metadata,
removing the need for hardcoded entity lists in retrieval code.

Entities shape query rewriting, so a catalog only ever holds names from
documents its owner may read: the shared corpus (``user_id IS NULL``) plus
that owner's own uploads. One catalog is cached per owner key.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from sqlalchemy import or_, select

from app.infrastructure.db.models import Document, DocumentProfile
from app.infrastructure.db.session import get_session_factory
from app.services.retrieval.search.vietnamese_orthography import (
    generate_orthographic_variants,
    normalize_unicode,
)

logger = logging.getLogger(__name__)

_FAILURE_RETRY_SECONDS = 30.0
_MAX_CATALOGS = 128


@dataclass
class CatalogEntity:
    """An entity discovered dynamically from the document repository."""

    name: str
    category: str  # "person", "document_type", "authority"
    variants: list[str] = field(default_factory=list)


class DynamicEntityCatalog:
    """In-memory cached catalog of entities from the documents one owner can read.

    ``owner_id`` ``None`` means the shared corpus only. Safe for concurrent
    coroutines (an ``asyncio.Lock`` guards refreshes), with a TTL cache.
    """

    def __init__(self, owner_id: str | None = None, ttl_seconds: float = 600.0) -> None:
        self._owner_id = owner_id
        self._ttl_seconds = ttl_seconds
        self._last_refresh_time: float = 0.0
        self._entities: dict[str, CatalogEntity] = {}  # lowercase_canonical -> CatalogEntity
        self._loaded = False
        self._lock = asyncio.Lock()

    @property
    def is_expired(self) -> bool:
        """Check if the cache has expired and needs a refresh."""
        return (time.time() - self._last_refresh_time) > self._ttl_seconds

    async def ensure_loaded(self) -> None:
        """Load from the DB once per TTL — also when the result was empty.

        An empty corpus (or a failed refresh) must not trigger a full reload on
        every query, so "loaded" is tracked separately from "has entities".
        """
        if not self._loaded or self.is_expired:
            async with self._lock:
                if not self._loaded or self.is_expired:
                    await self.refresh()

    async def refresh(self) -> None:
        """Rebuild the catalog from ``document_profiles`` of readable active documents.

        Works for every document kind: people (signers, authors, supervisors,
        chairs, attendees), issuing organisations and document subtypes.
        """
        owner_scope = (
            Document.user_id.is_(None)
            if self._owner_id is None
            else or_(Document.user_id.is_(None), Document.user_id == self._owner_id)
        )
        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                rows = (
                    await session.execute(
                        select(
                            DocumentProfile.entities,
                            DocumentProfile.issuer,
                            DocumentProfile.doc_subtype,
                        )
                        .join(Document, Document.id == DocumentProfile.document_id)
                        .where(Document.is_active.is_(True), owner_scope)
                    )
                ).all()

            new_entities: dict[str, CatalogEntity] = {}

            def add(name: str | None, category: str, *, variants: bool) -> None:
                if not name or not isinstance(name, str) or len(name.strip()) < 3:
                    return
                clean = normalize_unicode(name.strip())
                low = clean.lower()
                if low in new_entities:
                    return
                new_entities[low] = CatalogEntity(
                    name=clean,
                    category=category,
                    variants=generate_orthographic_variants(clean) if variants else [low],
                )

            for entities, issuer, subtype in rows:
                for ent in entities or []:
                    if isinstance(ent, dict) and ent.get("kind") != "party":
                        add(ent.get("name"), "person", variants=True)
                add(issuer, "authority", variants=False)
                add(subtype, "document_type", variants=False)

            self._entities = new_entities
            logger.info(
                "DynamicEntityCatalog refreshed: %d entities from %d profiles.",
                len(self._entities),
                len(rows),
            )
            self._last_refresh_time = time.time()
        except Exception as err:  # noqa: BLE001 - catalog is an optional enrichment
            logger.warning("Failed to refresh DynamicEntityCatalog from DB: %s", err)
            # Back off instead of retrying on every query while the DB is down.
            self._last_refresh_time = time.time() - self._ttl_seconds + _FAILURE_RETRY_SECONDS
        self._loaded = True

    def match_entities(self, query: str) -> list[tuple[str, list[str]]]:
        """Match entities in user query against dynamically loaded catalog.

        Returns:
            list[tuple[str, list[str]]]: List of (canonical_name, list_of_spelling_variants).
        """
        norm_query = normalize_unicode(query).lower()
        matched: list[tuple[str, list[str]]] = []
        matched_keys: set[str] = set()

        for key, entity in self._entities.items():
            for variant in entity.variants:
                if variant in norm_query:
                    if key not in matched_keys:
                        matched_keys.add(key)
                        matched.append((entity.name.lower(), entity.variants))
                    break

        return matched


_catalogs: OrderedDict[str | None, DynamicEntityCatalog] = OrderedDict()


def get_entity_catalog(owner_id: str | None = None) -> DynamicEntityCatalog:
    """The cached catalog for ``owner_id`` (``None``: shared corpus only).

    The least recently used catalog is dropped past ``_MAX_CATALOGS`` owners.
    """
    catalog = _catalogs.get(owner_id)
    if catalog is None:
        catalog = DynamicEntityCatalog(owner_id)
        _catalogs[owner_id] = catalog
        if len(_catalogs) > _MAX_CATALOGS:
            _catalogs.popitem(last=False)
    else:
        _catalogs.move_to_end(owner_id)
    return catalog


def invalidate_entity_catalog() -> None:
    """Drop every cached catalog so the next query reloads from the DB."""
    _catalogs.clear()
