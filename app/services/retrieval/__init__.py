"""P10 retrieval engine surface: foundation (P10A) + processing pipeline (P10B)."""

from app.domain.models.retrieval import (
    Evidence,
    EvidenceBundle,
    EvidenceUnitKind,
    ExpansionPolicy,
    RetrievalMode,
    RetrievalQuery,
    RetrievedChunk,
)
from app.services.retrieval.dense import DenseRetrievalService
from app.services.retrieval.diversity import (
    apply_diversity,
    apply_per_document_caps,
    dedup_candidates,
    suppress_near_duplicates,
)
from app.services.retrieval.expansion import (
    MAX_EXPANSION_PARENTS,
    ExpansionService,
    is_table_child,
    resolve_expansion_policy,
)
from app.services.retrieval.hybrid import DEFAULT_RRF_K, HybridRetrievalService
from app.services.retrieval.packing import build_bundle, citation_anchors, unit_for_chunk
from app.services.retrieval.pipeline import DEFAULT_RERANK_TOP_K_MAX, RetrievalPipeline
from app.services.retrieval.provider import RowProvider, SqlAlchemyRowProvider
from app.services.retrieval.rerank import IdentityReranker, Reranker
from app.services.retrieval.sparse import SparseRetrievalService

__all__ = [
    "DEFAULT_RERANK_TOP_K_MAX",
    "DEFAULT_RRF_K",
    "MAX_EXPANSION_PARENTS",
    "DenseRetrievalService",
    "Evidence",
    "EvidenceBundle",
    "EvidenceUnitKind",
    "ExpansionPolicy",
    "ExpansionService",
    "HybridRetrievalService",
    "IdentityReranker",
    "RetrievalMode",
    "RetrievalPipeline",
    "RetrievalQuery",
    "RetrievedChunk",
    "Reranker",
    "RowProvider",
    "SparseRetrievalService",
    "SqlAlchemyRowProvider",
    "apply_diversity",
    "apply_per_document_caps",
    "build_bundle",
    "citation_anchors",
    "dedup_candidates",
    "is_table_child",
    "resolve_expansion_policy",
    "suppress_near_duplicates",
    "unit_for_chunk",
]
