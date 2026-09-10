"""Expansion policies + context-unit builders (spec P10-08..P10-12).

Deterministic rules only ("no LLM when code is enough"):

- explicit ``RetrievalQuery.expansion_policy`` always wins;
- otherwise question-intent hints map to PARENT;
- everything else defaults to NONE (NEIGHBORS stays reachable purely via
  explicit override — agent strategies own it; future dynamic routing hints
  can be registered without changing the core protocol);

Parent grouping enforces one Evidence per parent id (P10-09) with the simple
documented scoring baseline ``best_child_score + bonus * min(extra_hits, 5)``
(P10-10, based on rerank_score if present, constant recorded for P10D ablations).
TABLE_CHILD candidates are never swapped for their parent (P10-12): they become
standalone TABLE_CHILD evidence carrying their heading-path caption context instead,
interleaved seamlessly with other units according to their ranked score.
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
    preferred_filename,
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


def _chunk_score(chunk: RetrievedChunk) -> float:
    return chunk.rerank_score if chunk.rerank_score is not None else chunk.score


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
        *,
        trace_id: str | None = None,
    ) -> list[Evidence]:
        """Dispatch per policy; maintains relative ranked priority for TABLE_CHILD candidates."""
        if policy is ExpansionPolicy.NONE:
            units = [unit_for_chunk(chunk) for chunk in ranked]
            logger.debug(
                "expansion_built",
                extra={
                    "policy": policy.value,
                    "units": len(units),
                    "retrieval_trace_id": trace_id,
                },
            )
            return units

        table_scored: list[tuple[float, int, Evidence]] = [
            (_chunk_score(chunk), idx, unit_for_chunk(chunk, kind="TABLE_CHILD"))
            for idx, chunk in enumerate(ranked)
            if is_table_child(chunk)
        ]
        orphan_scored: list[tuple[float, int, Evidence]] = [
            (_chunk_score(chunk), idx, unit_for_chunk(chunk, kind="CHILD"))
            for idx, chunk in enumerate(ranked)
            if not is_table_child(chunk) and chunk.parent_id is None
        ]
        core = [
            (idx, chunk)
            for idx, chunk in enumerate(ranked)
            if not is_table_child(chunk) and chunk.parent_id is not None
        ]

        if policy is ExpansionPolicy.PARENT:
            expanded_scored = await self._parent_units(core, query)
        else:  # NEIGHBORS
            expanded_scored = await self._neighbor_units(core, query)

        # Merge and sort all units by (priority_score DESC, original_rank_index ASC)
        all_scored = [*table_scored, *orphan_scored, *expanded_scored]
        all_scored.sort(key=lambda item: (-item[0], item[1]))
        units = [item[2] for item in all_scored]

        logger.debug(
            "expansion_built",
            extra={
                "policy": policy.value,
                "units": len(units),
                "retrieval_trace_id": trace_id,
            },
        )
        return units

    async def _parent_units(
        self, ranked_indexed: list[tuple[int, RetrievedChunk]], query: RetrievalQuery
    ) -> list[tuple[float, int, Evidence]]:
        """Resolve child -> parent payloads, deduped, priority-ordered (P10-09, P10-10)."""
        groups: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for idx, chunk in ranked_indexed:
            pid = chunk.parent_id
            if pid is None:
                logger.debug("orphan_child_skipped_from_parent_expansion")
                continue
            score = _chunk_score(chunk)
            entry = groups.get(pid)
            if entry is None:
                entry = {
                    "best": score,
                    "min_index": idx,
                    "hits": [chunk],
                    "document_id": chunk.document_id,
                }
                groups[pid] = entry
                order.append(pid)
            else:
                entry["best"] = max(entry["best"], score)
                entry["hits"].append(chunk)
        if not order:
            return []

        ordered = sorted(
            order,
            key=lambda pid: (
                -_priority(groups[pid]["best"], len(groups[pid]["hits"])),
                groups[pid]["min_index"],
            ),
        )
        selected = ordered[:MAX_EXPANSION_PARENTS]
        rows = await self._fetch(
            PARENT_FETCH_TEMPLATE.format(
                parent_scope=parent_scope_sql(selected),
                owner_scope=owner_scope_sql(query.requester_id),
            )
        )
        payload_by_pid = {str(row["chunk_id"]): row for row in rows}

        evidences: list[tuple[float, int, Evidence]] = []
        for pid in selected:
            entry = groups[pid]
            row = payload_by_pid.get(pid)
            if row is None:
                logger.warning("parent_payload_missing", extra={"parent_id": pid})
                continue
            best_hit = max(entry["hits"], key=_chunk_score)
            content_raw = str(row["content_raw"])
            priority_score = _priority(entry["best"], len(entry["hits"]))
            parent_anchors = chunk_anchor_metadata(row) or dict(best_hit.metadata)
            doc_ver_id = (
                row.get("document_version_id")
                or row.get("document_id")
                or best_hit.metadata.get("document_version_id")
                or best_hit.document_id
            )
            doc_ver = (
                row.get("version_number")
                if row.get("version_number") is not None
                else best_hit.metadata.get("version_number")
            )
            anchors = citation_anchors(parent_anchors)
            if doc_ver is not None and anchors.get("version_number") is None:
                anchors["version_number"] = doc_ver
            evidence = Evidence(
                kind="PARENT",
                content_raw=content_raw,
                token_estimate=estimate_tokens(content_raw),
                primary_chunk_id=pid,
                document_id=str(row["document_id"]),
                document_version_id=str(doc_ver_id) if doc_ver_id is not None else None,
                parent_id=pid,
                chunk_ids=[hit.chunk_id for hit in entry["hits"]],
                heading_path=coerce_heading_list(row.get("heading_path")),
                anchors=anchors,
                score=best_hit.score,
                rerank_score=best_hit.rerank_score,
                title=row.get("document_title") or best_hit.metadata.get("document_title"),
                filename=preferred_filename(row, best_hit.metadata),
                source_type=row.get("source_type") or best_hit.metadata.get("source_type"),
            )
            evidences.append((priority_score, entry["min_index"], evidence))
        return evidences

    async def _neighbor_units(
        self, ranked_indexed: list[tuple[int, RetrievedChunk]], query: RetrievalQuery
    ) -> list[tuple[float, int, Evidence]]:
        """prev/hit/next windows strictly within each affected parent (P10-11)."""
        parent_order: list[str] = []
        hits_by_parent: dict[str, list[RetrievedChunk]] = {}
        parent_min_index: dict[str, int] = {}
        for idx, chunk in ranked_indexed:
            pid = chunk.parent_id
            if pid is None:
                continue
            bucket = hits_by_parent.setdefault(pid, [])
            if not bucket:
                parent_order.append(pid)
                parent_min_index[pid] = idx
            bucket.append(chunk)
        if not parent_order:
            return []

        selected = parent_order[:MAX_EXPANSION_PARENTS]
        siblings = await self._fetch(
            SIBLING_FETCH_TEMPLATE.format(
                sibling_scope=sibling_scope_sql(selected),
                owner_scope=owner_scope_sql(query.requester_id),
            )
        )

        evidences: list[tuple[float, int, Evidence]] = []
        for pid in selected:
            rows = [row for row in siblings if str(row["parent_id"]) == pid]
            if not rows:
                continue
            by_index: dict[int, dict[str, Any]] = {}
            id_to_row: dict[str, dict[str, Any]] = {}
            for row in rows:
                by_index[int(row["chunk_index"])] = row
                id_to_row[str(row["chunk_id"])] = row

            parent_hits = hits_by_parent[pid]
            valid_hits = [hit for hit in parent_hits if hit.chunk_id in id_to_row]
            if not valid_hits:
                continue
            best_hit = max(valid_hits, key=_chunk_score)
            lead_row = id_to_row[best_hit.chunk_id]

            hit_indices = sorted(
                {int(id_to_row[hit.chunk_id]["chunk_index"]) for hit in valid_hits}
            )
            window = {
                index
                for hit in hit_indices
                for index in (hit - 1, hit, hit + 1)
                if index in by_index
            }
            if not window:
                continue
            members = [by_index[index] for index in sorted(window)]
            heading_path = coerce_heading_list(lead_row.get("heading_path")) or coerce_heading_list(
                members[0].get("heading_path")
            )
            merged_text = "\n\n".join(str(member["content_raw"]) for member in members)
            lead_anchors = chunk_anchor_metadata(lead_row) or dict(best_hit.metadata)
            doc_ver_id = (
                lead_row.get("document_version_id")
                or lead_row.get("document_id")
                or best_hit.metadata.get("document_version_id")
                or best_hit.document_id
            )
            doc_ver = (
                lead_row.get("version_number")
                if lead_row.get("version_number") is not None
                else best_hit.metadata.get("version_number")
            )
            anchors = citation_anchors(lead_anchors)
            if doc_ver is not None and anchors.get("version_number") is None:
                anchors["version_number"] = doc_ver
            evidence = Evidence(
                kind="NEIGHBOR_GROUP",
                content_raw=merged_text,
                token_estimate=estimate_tokens(merged_text),
                primary_chunk_id=best_hit.chunk_id,
                document_id=str(lead_row["document_id"]),
                document_version_id=str(doc_ver_id) if doc_ver_id is not None else None,
                parent_id=pid,
                chunk_ids=[str(member["chunk_id"]) for member in members],
                heading_path=heading_path,
                anchors=anchors,
                score=best_hit.score,
                rerank_score=best_hit.rerank_score,
                title=lead_row.get("document_title") or best_hit.metadata.get("document_title"),
                filename=preferred_filename(lead_row, best_hit.metadata),
                source_type=lead_row.get("source_type") or best_hit.metadata.get("source_type"),
            )
            best_score = _chunk_score(best_hit)
            evidences.append((best_score, parent_min_index[pid], evidence))
        return evidences

    async def _fetch(self, sql: str) -> list[dict[str, Any]]:
        return await self._provider.fetch(sql)
