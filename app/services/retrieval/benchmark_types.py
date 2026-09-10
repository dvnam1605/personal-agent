"""Benchmark corpus *types* used by the retrieval evaluation runner.

The curated query/document corpus lives in tests/fixtures/retrieval_benchmark_dataset.py
so production imports do not load 1k+ lines of eval data.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.retrieval import ExpansionPolicy, RetrievalMode, RetrievalQuery
from app.domain.models.retrieval.sufficiency import SufficiencyStatus


class BenchmarkQueryCategory(StrEnum):
    """The 11 mandatory benchmark categories defined in spec P10-21."""

    EXACT_KEYWORD = "exact_keyword"
    SEMANTIC_PARAPHRASE = "semantic_paraphrase"
    MIXED_EN_VI = "mixed_en_vi"
    HEADING_SPECIFIC = "heading_specific"
    TABLE_SPECIFIC = "table_specific"
    DOCUMENT_LOCAL = "document_local"
    CROSS_DOC_COMPARE = "cross_doc_compare"
    NEGATIVE_NO_ANSWER = "negative_no_answer"
    SOURCE_FILTERED = "source_filtered"
    BROAD_WHY_EXPLAIN = "broad_why_explain"
    SPECIFIC_FACT = "specific_fact"


class BenchmarkQuery(BaseModel):
    """A test query with ground-truth labels and evaluation expectations."""

    model_config = ConfigDict(frozen=True)

    query_id: str
    category: BenchmarkQueryCategory
    query_text: str
    description: str = ""
    mode: RetrievalMode = RetrievalMode.CORPUS_SEARCH
    expansion_policy: ExpansionPolicy = ExpansionPolicy.PARENT
    document_ids: list[str] = Field(default_factory=list)
    source_filters: dict[str, Any] = Field(default_factory=dict)

    # Ground-truth expectations
    expected_doc_ids: list[str] = Field(default_factory=list)
    expected_parent_ids: list[str] = Field(default_factory=list)
    expected_child_ids: list[str] = Field(default_factory=list)
    expected_sufficiency: SufficiencyStatus = SufficiencyStatus.SUFFICIENT

    def to_retrieval_query(
        self, requester_id: str = "00000000-0000-0000-0000-000000000001"
    ) -> RetrievalQuery:
        """Convert benchmark query definition into pipeline's RetrievalQuery."""
        return RetrievalQuery(
            original_query=self.query_text,
            search_query=self.query_text,
            mode=self.mode,
            expansion_policy=self.expansion_policy,
            document_ids=self.document_ids,
            source_filters=self.source_filters,
            requester_id=requester_id,
        )


class BenchmarkChunk(BaseModel):
    """A chunk within the benchmark corpus."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    parent_id: str | None = None
    hierarchy_level: int  # 0 for PARENT, 1 for CHILD
    node_type: str = "text"  # "text", "heading", "table", "TABLE_CHILD", "section"
    heading_path: list[str] = Field(default_factory=list)
    chunk_index: int = 0
    page_start: int | None = 1
    page_end: int | None = 1
    content_raw: str
    keywords: list[str] = Field(default_factory=list)
    administrative_metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkDocument(BaseModel):
    """A document within the benchmark corpus."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    title: str
    uri: str
    source_type: str = "pdf"
    version_number: int = 1
    description: str = ""
    administrative_metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkCorpus(BaseModel):
    """A complete self-contained corpus for deterministic evaluation."""

    model_config = ConfigDict(frozen=True)

    documents: list[BenchmarkDocument]
    chunks: list[BenchmarkChunk]

    def get_document(self, doc_id: str) -> BenchmarkDocument | None:
        for doc in self.documents:
            if doc.document_id == doc_id:
                return doc
        return None

    def get_chunk(self, chunk_id: str) -> BenchmarkChunk | None:
        """Find a chunk by its chunk_id."""
        for c in self.chunks:
            if c.chunk_id == chunk_id:
                return c
        return None

    def get_children(self, doc_id: str | None = None) -> list[BenchmarkChunk]:
        """Return CHILD (level 1) chunks."""
        return [
            c
            for c in self.chunks
            if c.hierarchy_level == 1 and (doc_id is None or c.document_id == doc_id)
        ]

    def get_parents(self, doc_id: str | None = None) -> list[BenchmarkChunk]:
        """Return PARENT (level 0) chunks."""
        return [
            c
            for c in self.chunks
            if c.hierarchy_level == 0 and (doc_id is None or c.document_id == doc_id)
        ]
