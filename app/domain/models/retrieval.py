"""Retrieval domain contracts (spec P10-01..P10-05).

Frozen typed models shared by the retrieval services: query intent, the
canonical retrieved-chunk shape returned by every retriever, and the rank
bookkeeping required by RRF fusion (dense_rank / sparse_rank / fusion_score /
retrieval_sources are tracked end-to-end per spec P10-05).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetrievalMode(StrEnum):
    """Retrieval modes (spec P10-01). METADATA_LOOKUP never enters RAG."""

    CORPUS_SEARCH = "CORPUS_SEARCH"
    DOCUMENT_SEARCH = "DOCUMENT_SEARCH"
    COMPARE_DOCUMENTS = "COMPARE_DOCUMENTS"
    METADATA_LOOKUP = "METADATA_LOOKUP"


class ExpansionPolicy(StrEnum):
    """Context expansion policies (spec P10-08; behaviour lands in P10B)."""

    NONE = "NONE"
    NEIGHBORS = "NEIGHBORS"
    PARENT = "PARENT"


class RetrievalQuery(BaseModel):
    """Everything a retriever needs for one search (spec P10-02).

    ``search_query`` defaults to ``original_query``: resolve conversational
    references upstream and do NOT rewrite with an LLM when the query is
    already retrieval-ready.
    """

    model_config = ConfigDict(frozen=True)

    original_query: str = Field(min_length=1)
    search_query: str = Field(min_length=1)
    mode: RetrievalMode = RetrievalMode.CORPUS_SEARCH
    document_ids: list[str] = Field(default_factory=list)
    source_filters: dict[str, Any] = Field(default_factory=dict)
    requester_id: str | None = None
    top_k_dense: int = Field(default=20, ge=1, le=100)
    top_k_sparse: int = Field(default=20, ge=1, le=100)
    limit: int = Field(default=10, ge=1, le=100)
    # Caller-supplied packing budget (spec P10-08/13: expansion + packing under
    # an explicit token/latency budget; hard max respected byte-exact).
    context_token_budget: int = Field(default=4096, ge=128, le=100_000)
    require_source_diversity: bool = False
    expansion_policy: ExpansionPolicy | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_search_query(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("search_query"):
            original = data.get("original_query")
            if original:
                data = {**data, "search_query": original}
        return data

    @model_validator(mode="after")
    def _document_modes_require_ids(self) -> RetrievalQuery:
        if self.mode in (RetrievalMode.DOCUMENT_SEARCH, RetrievalMode.COMPARE_DOCUMENTS):
            if not self.document_ids:
                raise ValueError(f"{self.mode.value} requires at least one document_id")
        if self.mode is RetrievalMode.METADATA_LOOKUP:
            # P10-01: metadata/filename questions resolve through the Drive
            # metadata path — semantic retrieval must reject them at the
            # contract boundary instead of silently searching embeddings.
            raise ValueError("METADATA_LOOKUP must not enter the RAG retrieval path")
        return self


class RetrievedChunk(BaseModel):
    """Canonical candidate shape returned by dense, sparse and hybrid (P10-03/04)."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    parent_id: str | None = None
    document_id: str
    content_raw: str
    score: float
    retrieval_type: Literal["dense", "sparse", "hybrid"]
    dense_rank: int | None = None
    sparse_rank: int | None = None
    fusion_score: float | None = None
    # P10-07 bookkeeping: model/version tag + pre/post ranks (set by rerankers)
    rerank_score: float | None = None
    rerank_rank: int | None = None
    rerank_model: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


EvidenceUnitKind = Literal["CHILD", "NEIGHBOR_GROUP", "PARENT", "TABLE_CHILD"]


class Evidence(BaseModel):
    """One mixed-unit context item for generation (spec P10-13)."""

    model_config = ConfigDict(frozen=True)

    kind: EvidenceUnitKind
    content_raw: str
    token_estimate: int
    primary_chunk_id: str
    document_id: str
    parent_id: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)
    heading_path: list[str] = Field(default_factory=list)
    anchors: dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(BaseModel):
    """Budget-respecting packed context output (spec P10-13, normative shape)."""

    model_config = ConfigDict(frozen=True)

    items: list[Evidence]
    total_tokens: int
    documents_used: list[str]
    parent_ids_used: list[str]
    retrieval_trace_id: str
