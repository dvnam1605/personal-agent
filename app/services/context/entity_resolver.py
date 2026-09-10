"""EntityResolver engine resolving surface references and conversation anaphora (spec P17-01, P17-02)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.domain.enums import EntityType
from app.domain.models.entity import EntityRecord, EntityResolutionResult
from app.domain.models.evidence import EvidenceItem
from app.services.context.entity_store import EntityStore

_VIETNAMESE_NUMBERS: dict[str, int] = {
    "một": 1,
    "hai": 2,
    "ba": 3,
    "bốn": 4,
    "năm": 5,
    "sáu": 6,
    "bảy": 7,
    "tám": 8,
    "chín": 9,
    "mười": 10,
}

_DEICTIC_PATTERNS: list[tuple[re.Pattern[str], EntityType, str]] = [
    (
        re.compile(r"\b(email|thư|mail)\s+(đó|kia|vừa rồi|vừa gửi|này)\b", re.IGNORECASE),
        EntityType.EMAIL,
        "email reference",
    ),
    (
        re.compile(r"\b(file|tài liệu|văn bản|báo cáo)\s+(đó|kia|vừa rồi|này)\b", re.IGNORECASE),
        EntityType.DOCUMENT,
        "document reference",
    ),
    (
        re.compile(
            r"\b(cuộc họp|lịch họp|buổi họp|sự kiện)\s+(vừa nói|vừa rồi|đó|kia|này)\b",
            re.IGNORECASE,
        ),
        EntityType.EVENT,
        "event reference",
    ),
    (
        re.compile(r"\b(dự án|project)\s+(đó|kia|vừa rồi|này)\b", re.IGNORECASE),
        EntityType.PROJECT,
        "project reference",
    ),
]

_QUANTITY_PATTERN = re.compile(
    r"\b(\d+|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười)\s+(tài liệu|file|văn bản|email|cuộc họp)\s+(đó|kia|trên|vừa rồi|này)\b",
    re.IGNORECASE,
)


class EntityResolver:
    """Resolves named entities, ambiguous entities, and conversation references."""

    def __init__(self, store: EntityStore) -> None:
        self._store = store

    async def resolve_entities(
        self,
        query: str,
        user_id: str,
        conversation_context: Sequence[EvidenceItem | dict[str, Any] | EntityRecord] | None = None,
    ) -> list[EntityResolutionResult]:
        """Resolve all detectable entity references in the user query."""
        results: list[EntityResolutionResult] = []
        cleaned_query = query.strip()
        if not cleaned_query:
            return results

        # 1. Check for quantity references (e.g. "ba tài liệu trên", "2 email đó")
        qty_matches = list(_QUANTITY_PATTERN.finditer(cleaned_query))
        for match in qty_matches:
            qty_raw = match.group(1).lower()
            kind_raw = match.group(2).lower()
            phrase = match.group(0)

            count = int(qty_raw) if qty_raw.isdigit() else _VIETNAMESE_NUMBERS.get(qty_raw, 1)
            target_type = EntityType.DOCUMENT
            if "email" in kind_raw:
                target_type = EntityType.EMAIL
            elif "cuộc họp" in kind_raw:
                target_type = EntityType.EVENT

            matched_items = self._resolve_from_conversation_context(
                target_type, count, conversation_context or [], user_id
            )
            results.append(
                EntityResolutionResult(
                    query_reference=phrase,
                    resolved_entities=matched_items,
                    is_ambiguous=len(matched_items) < count if matched_items else False,
                    candidate_entities=matched_items,
                    confidence=0.9 if matched_items else 0.4,
                    resolution_notes=f"Resolved {len(matched_items)} items for quantity reference '{phrase}'",
                )
            )

        # 2. Check for singular deictic references (e.g. "email đó", "file kia", "cuộc họp vừa nói")
        for pattern, ent_type, label in _DEICTIC_PATTERNS:
            match = pattern.search(cleaned_query)
            if match:
                phrase = match.group(0)
                # Avoid re-resolving if already caught by quantity match
                if any(phrase in r.query_reference for r in results):
                    continue
                matched_items = self._resolve_from_conversation_context(
                    ent_type, 1, conversation_context or [], user_id
                )
                results.append(
                    EntityResolutionResult(
                        query_reference=phrase,
                        resolved_entities=matched_items[:1],
                        is_ambiguous=False,
                        candidate_entities=matched_items,
                        confidence=0.95 if matched_items else 0.5,
                        resolution_notes=f"Resolved {label} '{phrase}'",
                    )
                )

        # 3. Named entity lookup: inspect registered user entities in the store
        # Extract potential proper names / capitalized tokens or words
        known_entities = await self._store.list_entities(user_id)
        query_lower = cleaned_query.lower()

        # Group matches by search token to detect ambiguity (e.g. two people with alias 'Nam')
        found_tokens: dict[str, list[EntityRecord]] = {}

        for ent in known_entities:
            tokens_to_check = [ent.canonical_name.lower(), *[a.lower() for a in ent.aliases]]
            for tok in tokens_to_check:
                # Check for word boundary match
                if re.search(rf"\b{re.escape(tok)}\b", query_lower):
                    found_tokens.setdefault(tok, [])
                    if ent not in found_tokens[tok]:
                        found_tokens[tok].append(ent)

        # Prune tokens that are substrings/sub-phrases of longer matched tokens (e.g. 'nam' is subsumed by 'anh nam')
        all_matched_tokens = sorted(found_tokens.keys(), key=len, reverse=True)
        active_tokens: list[str] = []
        for tok in all_matched_tokens:
            if not any(re.search(rf"\b{re.escape(tok)}\b", longer) for longer in active_tokens):
                active_tokens.append(tok)

        for tok in active_tokens:
            matched_records = found_tokens[tok]
            # If multiple records match the same token, flag ambiguity
            if len(matched_records) > 1:
                results.append(
                    EntityResolutionResult(
                        query_reference=tok,
                        resolved_entities=[],
                        is_ambiguous=True,
                        candidate_entities=matched_records,
                        confidence=0.5,
                        resolution_notes=f"Ambiguous reference: found {len(matched_records)} candidate entities for '{tok}'",
                    )
                )
            elif len(matched_records) == 1:
                rec = matched_records[0]
                results.append(
                    EntityResolutionResult(
                        query_reference=tok,
                        resolved_entities=[rec],
                        is_ambiguous=False,
                        candidate_entities=[rec],
                        confidence=rec.confidence,
                        resolution_notes=f"Resolved exact match for '{tok}' as '{rec.canonical_name}'",
                    )
                )

        return results

    def _resolve_from_conversation_context(
        self,
        target_type: EntityType,
        count: int,
        context: Sequence[EvidenceItem | dict[str, Any] | EntityRecord],
        user_id: str,
    ) -> list[EntityRecord]:
        """Extract top-N most recent matching records from context."""
        extracted: list[EntityRecord] = []
        for item in reversed(context):
            if len(extracted) >= count:
                break
            if isinstance(item, EntityRecord):
                if item.entity_type == target_type:
                    extracted.append(item)
            elif isinstance(item, EvidenceItem):
                record = self._evidence_to_entity(item, target_type, user_id)
                if record is not None:
                    extracted.append(record)
            elif isinstance(item, dict):
                # Dict representation
                doc_type = str(item.get("type", "")).upper()
                if target_type.value in doc_type or item.get("entity_type") == target_type.value:
                    extracted.append(
                        EntityRecord(
                            id=str(item.get("id", f"ctx_{len(extracted)}")),
                            user_id=user_id,
                            entity_type=target_type,
                            canonical_name=str(
                                item.get("title") or item.get("name") or "Context item"
                            ),
                            attributes=item,
                        )
                    )
        return extracted

    @staticmethod
    def _evidence_to_entity(
        ev: EvidenceItem, target_type: EntityType, user_id: str
    ) -> EntityRecord | None:
        """Map grounded evidence item to an EntityRecord if compatible."""
        ev_type_str = str(ev.evidence_type).lower()
        if target_type == EntityType.DOCUMENT and (
            "document" in ev_type_str or "file" in ev_type_str
        ):
            title = ev.source.title or f"Document {ev.source.source_id}"
            return EntityRecord(
                id=ev.id,
                user_id=user_id,
                entity_type=EntityType.DOCUMENT,
                canonical_name=title,
                attributes={"content": ev.content, "source_id": ev.source.source_id},
            )
        if target_type == EntityType.EMAIL and "email" in ev_type_str:
            title = ev.source.title or f"Email {ev.source.source_id}"
            return EntityRecord(
                id=ev.id,
                user_id=user_id,
                entity_type=EntityType.EMAIL,
                canonical_name=title,
                attributes={"content": ev.content, "source_id": ev.source.source_id},
            )
        if target_type == EntityType.EVENT and (
            "event" in ev_type_str or "calendar" in ev_type_str
        ):
            title = ev.source.title or f"Event {ev.source.source_id}"
            return EntityRecord(
                id=ev.id,
                user_id=user_id,
                entity_type=EntityType.EVENT,
                canonical_name=title,
                attributes={"content": ev.content, "source_id": ev.source.source_id},
            )
        if target_type == EntityType.PROJECT and (
            "project" in ev_type_str
            or "project" in (ev.source.title or "").lower()
            or "dự án" in (ev.source.title or "").lower()
        ):
            title = ev.source.title or f"Project {ev.source.source_id}"
            return EntityRecord(
                id=ev.id,
                user_id=user_id,
                entity_type=EntityType.PROJECT,
                canonical_name=title,
                attributes={"content": ev.content, "source_id": ev.source.source_id},
            )
        return None
