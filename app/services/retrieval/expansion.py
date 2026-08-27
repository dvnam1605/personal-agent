"""Expansion policies + context-unit builders (spec P10-08..P10-12).

Deterministic rules only ("no LLM when code is enough"):

- explicit ``RetrievalQuery.expansion_policy`` always wins;
- otherwise question-intent hints map to PARENT;
- everything else defaults to NONE (NEIGHBORS stays reachable purely via
  explicit override — agent strategies own it);

Parent grouping enforces one Evidence per parent id (P10-09) with the simple
documented scoring baseline ``best_child_score + bonus * min(extra_hits, 5)``
(P10-10, constant recorded for P10D ablations). TABLE_CHILD candidates are
never swapped for their parent (P10-12): they become standalone TABLE_CHILD
evidence carrying their heading-path caption context instead.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

from app.domain.models.retrieval import (
    Evidence,
    ExpansionPolicy,
    RetrievalQuery,
    RetrievedChunk,
)
from app.services.ingestion.chunking.tokens import estimate_tokens
from app.services.retrieval.packing import citation_anchors, unit_for_chunk
from app.services.retrieval.provider import RowProvider, SqlAlchemyRowProvider
from app.services.retrieval.sql import (
    PARENT_FETCH_TEMPLATE,
    SIBLING_FETCH_TEMPLATE,
    chunk_anchor_metadata,
    coerce_heading_list,
    owner_scope_sql,
    parent_scope_sql,
    sibling_scope_sql,
)

logger = logging.getLogger(__name__)

_QUESTION_HINTS = (
    "why",
    "explain",
    "tại sao",
    "giải thích",
    "so sánh",
    "phân tích",
    "compare",
)
_PARENT_PRIORITY_BONUS = 0.1  # documented baseline constant (P10-10)
_PARENT_BONUS_CAP_HITS = 5  # avoids runaway inflation on huge sections
MAX_EXPANSION_PARENTS = 32


def resolve_expansion_policy(query: RetrievalQuery) -> ExpansionPolicy:
    """Deterministic NONE/PARENT default; explicit override wins (P10-08).

    Hint matching folds case AND strips diacritics (mirroring the FTS unaccent
    posture) so unaccented Vietnamese questions still route to PARENT.
    """
    if query.expansion_policy is not None:
        return query.expansion_policy
    haystack = _strip_marks(query.search_query.casefold())
    if any(_strip_marks(hint) in haystack for hint in _QUESTION_HINTS):
        return ExpansionPolicy.PARENT
    return ExpansionPolicy.NONE


def _strip_marks(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def is_table_child(chunk: RetrievedChunk) -> bool:
    return chunk.metadata.get("node_type") == "TABLE_CHILD"


def _priority(best_score: float, hit_count: int) -> float:
    extras = min(hit_count - 1, _PARENT_BONUS_CAP_HITS)
    return best_score + _PARENT_PRIORITY_BONUS * max(extras, 0)


class ExpansionService:
    """Builds packable mixed units according to the resolved policy."""

    def __init__(self, provider: RowProvider | None = None) -> None:
        self._provider: RowProvider = provider or SqlAlchemyRowProvider()

    async def build_units(
        self,
        ranked: list[RetrievedChunk],
        policy: ExpansionPolicy,
        query: RetrievalQuery,
    ) -> list[Evidence]:
        """Dispatch per policy; TABLE_CHILD candidates stay standalone."""
        table_units = [
            unit_for_chunk(chunk, kind="TABLE_CHILD")
            for chunk in ranked
            if is_table_child(chunk)
        ]
        core = [chunk for chunk in ranked if not is_table_child(chunk)]

        units: list[Evidence] = []
        if policy is ExpansionPolicy.NONE:
            units.extend(unit_for_chunk(chunk) for chunk in core)
        elif policy is ExpansionPolicy.PARENT:
            units.extend(await self._parent_units(core, query))
        else:  # NEIGHBORS
            units.extend(await self._neighbor_units(core, query))
        logger.debug("expansion_built", extra={"policy": policy.value, "units": len(units)})
        return [*units, *table_units]

    async def _parent_units(
        self, ranked: list[RetrievedChunk], query: RetrievalQuery
    ) -> list[Evidence]:
        """Resolve child -> parent payloads, deduped, priority-ordered."""
        groups: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for chunk in ranked:
            pid = chunk.parent_id
            if pid is None:
                logger.debug("orphan_child_skipped_from_parent_expansion")
                continue
            entry = groups.get(pid)
            if entry is None:
                entry = {"best": chunk.score, "hits": [chunk], "document_id": chunk.document_id}
                groups[pid] = entry
                order.append(pid)
            else:
                entry["best"] = max(entry["best"], chunk.score)
                entry["hits"].append(chunk)
        if not order:
            return []

        ordered = sorted(
            order,
            key=lambda pid: -_priority(groups[pid]["best"], len(groups[pid]["hits"])),
        )
        selected = ordered[:MAX_EXPANSION_PARENTS]
        rows = await self._fetch(
            PARENT_FETCH_TEMPLATE.format(
                parent_ids=parent_scope_sql(selected),
                owner_scope=owner_scope_sql(query.requester_id),
            )
        )
        payload_by_pid = {str(row["chunk_id"]): row for row in rows}

        evidences: list[Evidence] = []
        for pid in selected:
            entry = groups[pid]
            row = payload_by_pid.get(pid)
            if row is None:
                logger.warning("parent_payload_missing", extra={"parent_id": pid})
                continue
            first_hit = entry["hits"][0]
            content_raw = str(row["content_raw"])
            evidences.append(
                Evidence(
                    kind="PARENT",
                    content_raw=content_raw,
                    token_estimate=estimate_tokens(content_raw),
                    primary_chunk_id=pid,
                    document_id=str(row["document_id"]),
                    parent_id=pid,
                    chunk_ids=[hit.chunk_id for hit in entry["hits"]],
                    heading_path=coerce_heading_list(row.get("heading_path")),
                    anchors=citation_anchors(dict(first_hit.metadata)),
                )
            )
        return evidences

    async def _neighbor_units(
        self, ranked: list[RetrievedChunk], query: RetrievalQuery
    ) -> list[Evidence]:
        """prev/hit/next windows strictly within each affected parent (P10-11)."""
        parent_order: list[str] = []
        hits_by_parent: dict[str, list[RetrievedChunk]] = {}
        for chunk in ranked:
            pid = chunk.parent_id
            if pid is None:
                continue
            bucket = hits_by_parent.setdefault(pid, [])
            if not bucket:
                parent_order.append(pid)
            bucket.append(chunk)
        if not parent_order:
            return []

        selected = parent_order[:MAX_EXPANSION_PARENTS]
        siblings = await self._fetch(
            SIBLING_FETCH_TEMPLATE.format(
                parent_ids=sibling_scope_sql(selected),
                owner_scope=owner_scope_sql(query.requester_id),
            )
        )

        evidences: list[Evidence] = []
        for pid in selected:
            rows = [row for row in siblings if str(row["parent_id"]) == pid]
            if not rows:
                continue
            by_index: dict[int, dict[str, Any]] = {}
            id_to_row: dict[str, dict[str, Any]] = {}
            for row in rows:
                by_index[int(row["chunk_index"])] = row
                id_to_row[str(row["chunk_id"])] = row

            hit_indices = sorted(
                {
                    int(id_to_row[hit.chunk_id]["chunk_index"])
                    for hit in hits_by_parent[pid]
                    if hit.chunk_id in id_to_row
                }
            )
            window = {
                index
                for hit in hit_indices
                for index in (hit - 1, hit, hit + 1)
                if index in by_index
            }
            if not window or not hit_indices:
                continue
            members = [by_index[index] for index in sorted(window)]
            lead = members[len(hit_indices)] if len(members) > 1 else members[-1]
            heading_path = coerce_heading_list(lead.get("heading_path")) or coerce_heading_list(
                members[0].get("heading_path")
            )
            merged_text = "\n\n".join(str(member["content_raw"]) for member in members)
            evidences.append(
                Evidence(
                    kind="NEIGHBOR_GROUP",
                    content_raw=merged_text,
                    token_estimate=estimate_tokens(merged_text),
                    primary_chunk_id=str(lead["chunk_id"]),
                    document_id=str(lead["document_id"]),
                    parent_id=pid,
                    chunk_ids=[str(member["chunk_id"]) for member in members],
                    heading_path=heading_path,
                    anchors=citation_anchors(chunk_anchor_metadata(members[0])),
                )
            )
        return evidences

    async def _fetch(self, sql: str) -> list[dict[str, Any]]:
        return await self._provider.fetch(sql)
