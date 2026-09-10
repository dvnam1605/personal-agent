"""P10 retrieval engine surface: foundation (P10A) + processing pipeline (P10B) + policies & safety (P10C)."""

from app.domain.models.citation import Citation
from app.domain.models.retrieval import (
    Evidence,
    EvidenceBundle,
    EvidenceUnitKind,
    ExpansionPolicy,
    RetrievalMode,
    RetrievalQuery,
    RetrievedChunk,
)
from app.domain.models.sufficiency import SufficiencyStatus, SufficiencyVerdict
from app.services.retrieval.compare_policy import CompareResult, enforce_compare_diversity
from app.services.retrieval.dense import DenseRetrievalService
from app.services.retrieval.diversity import (
    apply_diversity,
    apply_per_document_caps,
    dedup_candidates,
    suppress_near_duplicates,
)
from app.services.retrieval.evaluation import (
    AblationReport,
    AblationRunner,
    ChunkingAblationConfig,
    PipelineTimings,
    QueryEvaluationResult,
    SearchModeAblationConfig,
    calculate_mrr,
    calculate_ndcg_at_k,
    calculate_recall_at_k,
)
from app.services.retrieval.expansion import (
    MAX_EXPANSION_PARENTS,
    ExpansionService,
    is_table_child,
    resolve_expansion_policy,
)
from app.services.retrieval.factory import (
    build_embedding_service,
    build_reranker,
    build_retrieval_pipeline,
    resolve_embedding_snapshot_path,
)
from app.services.retrieval.hybrid import DEFAULT_RRF_K, HybridRetrievalService
from app.services.retrieval.injection_boundary import (
    BOUNDARY_INSTRUCTIONS,
    sanitize_evidence_for_prompt,
)
from app.services.retrieval.packing import build_bundle, citation_anchors, unit_for_chunk
from app.services.retrieval.pipeline import DEFAULT_RERANK_TOP_K_MAX, RetrievalPipeline
from app.services.retrieval.provider import RowProvider, SqlAlchemyRowProvider
from app.services.retrieval.rerank import IdentityReranker, Reranker, ViRankerReranker
from app.services.retrieval.retry import RetryPolicy, RetryStrategy, apply_retry_strategy
from app.services.retrieval.sparse import SparseRetrievalService
from app.services.retrieval.sufficiency import SufficiencyChecker
from app.services.retrieval.synthesis import (
    AnswerSynthesizer,
    PromptAnswerSynthesizer,
    SynthesisResult,
    build_citations_from_bundle,
    extract_cited_ids,
)

__all__ = [
    # P10A foundation
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
    "ViRankerReranker",
    "apply_diversity",
    "apply_per_document_caps",
    "build_bundle",
    "build_embedding_service",
    "citation_anchors",
    "dedup_candidates",
    "is_table_child",
    "resolve_expansion_policy",
    "suppress_near_duplicates",
    "unit_for_chunk",
    # P10C policies & safety
    "BOUNDARY_INSTRUCTIONS",
    "AnswerSynthesizer",
    "Citation",
    "CompareResult",
    "PromptAnswerSynthesizer",
    "RetryPolicy",
    "RetryStrategy",
    "SufficiencyChecker",
    "SufficiencyStatus",
    "SufficiencyVerdict",
    "SynthesisResult",
    "apply_retry_strategy",
    "build_citations_from_bundle",
    "build_reranker",
    "build_retrieval_pipeline",
    "enforce_compare_diversity",
    "extract_cited_ids",
    "resolve_embedding_snapshot_path",
    "sanitize_evidence_for_prompt",
    # P10D evaluation metrics (corpus lives in tests/fixtures/retrieval_benchmark_dataset.py)
    "AblationReport",
    "AblationRunner",
    "ChunkingAblationConfig",
    "PipelineTimings",
    "QueryEvaluationResult",
    "SearchModeAblationConfig",
    "calculate_mrr",
    "calculate_ndcg_at_k",
    "calculate_recall_at_k",
]
