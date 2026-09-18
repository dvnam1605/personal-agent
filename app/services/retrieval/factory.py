"""Settings-driven wiring for the retrieval pipeline (hybrid RRF + ViRanker).

Default production stack (ADR-0012, rag-retrieval-strategy §3-5)::

    Dense (pgvector HNSW) + Sparse (PostgreSQL FTS)
      → parallel RRF fusion (k=60) → diversity
      → ViRanker cross-encoder rerank → expansion → budget packing
      → sufficiency (calibrated threshold) → bounded retry.

``RetrievalPipeline`` keeps ``IdentityReranker`` + zero-threshold defaults so
unit tests stay deterministic and torch-free; use the builders here whenever
real settings should decide. All heavy model loads stay lazy (first
``embed``/``rerank`` call), so building a pipeline never touches torch/disk —
except :func:`resolve_embedding_snapshot_path`, which resolves the cached
snapshot path when ``EMBEDDING__LOCAL_PATH`` is unset.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings
from app.core.config import settings as global_settings
from app.services.ingestion.embedding import (
    EmbeddingBackend,
    EmbeddingContract,
    LocalEmbeddingService,
    TransformersPoolingBackend,
)
from app.services.retrieval.dense import DenseRetrievalService
from app.services.retrieval.hybrid import DEFAULT_RRF_K, HybridRetrievalService
from app.services.retrieval.pipeline import DEFAULT_RERANK_TOP_K_MAX, RetrievalPipeline
from app.services.retrieval.provider import RowProvider, SqlAlchemyRowProvider
from app.services.retrieval.rerank import IdentityReranker, Reranker, ViRankerReranker
from app.services.retrieval.retry import RetryPolicy
from app.services.retrieval.sparse import SparseRetrievalService
from app.services.retrieval.sufficiency import (
    DEFAULT_MIN_SCORE_THRESHOLD,
    SufficiencyChecker,
)
from app.services.retrieval.synthesis import (
    AnswerSynthesizer,
    GenerateCallback,
    PromptAnswerSynthesizer,
)

logger = logging.getLogger(__name__)


def resolve_embedding_snapshot_path(settings_obj: Settings | None = None) -> str:
    """Return the local embedding snapshot directory.

    ``EMBEDDING__LOCAL_PATH`` wins when set; otherwise resolve the cached
    HuggingFace snapshot for ``EMBEDDING__MODEL``. Falls back to downloading
    from HuggingFace Hub if local cache is missing.
    """
    from app.core.config import resolve_project_path

    cfg = (settings_obj or global_settings).embedding
    if cfg.local_path:
        return str(resolve_project_path(cfg.local_path))
    from huggingface_hub import snapshot_download

    try:
        return snapshot_download(cfg.model, local_files_only=True)
    except Exception as exc:  # noqa: BLE001 - fallback to remote download when local snapshot misses
        logger.warning(
            "Local embedding snapshot for '%s' not found. Falling back to HuggingFace Hub download: %s",
            cfg.model,
            exc,
        )
        return snapshot_download(cfg.model, local_files_only=False)


_shared_embedding_service: LocalEmbeddingService | None = None


def get_shared_embedding_service(
    settings_obj: Settings | None = None,
) -> LocalEmbeddingService:
    """Return the process-wide shared LocalEmbeddingService singleton."""
    global _shared_embedding_service
    if _shared_embedding_service is None:
        _shared_embedding_service = build_embedding_service(settings_obj)
    return _shared_embedding_service


def build_embedding_service(
    settings_obj: Settings | None = None,
    *,
    backend: EmbeddingBackend | None = None,
    batch_size: int | None = None,
) -> LocalEmbeddingService:
    """Build the local Vietnamese embedding service from settings."""
    resolved = settings_obj or global_settings
    cfg = resolved.embedding
    service_backend = backend or TransformersPoolingBackend(
        resolve_embedding_snapshot_path(resolved),
        device=(cfg.device.strip() if cfg.device else None),
    )
    return LocalEmbeddingService(
        backend=service_backend,
        contract=EmbeddingContract(model_name=cfg.model, dimensions=cfg.dimensions),
        batch_size=batch_size or cfg.batch_size,
    )


def build_reranker(
    settings_obj: Settings | None = None,
    *,
    use_viranker: bool = True,
) -> Reranker:
    """Build the production reranker: ViRanker cross-encoder by default."""
    if not use_viranker:
        return IdentityReranker()
    resolved = settings_obj or global_settings
    return ViRankerReranker.from_settings(resolved.reranker)


def build_retrieval_pipeline(
    settings_obj: Settings | None = None,
    provider: RowProvider | None = None,
    *,
    engine: Any | None = None,
    hybrid_service: Any | None = None,
    embedding_service: LocalEmbeddingService | None = None,
    reranker: Reranker | None = None,
    use_viranker: bool = True,
    rerank_top_k_max: int = DEFAULT_RERANK_TOP_K_MAX,
    per_document_cap: int | None = None,
    sufficiency_checker: SufficiencyChecker | None = None,
    retry_policy: RetryPolicy | None = None,
    synthesizer: AnswerSynthesizer | None = None,
    generate: GenerateCallback | None = None,
    rrf_k: int = DEFAULT_RRF_K,
) -> RetrievalPipeline:
    """Assemble the full hybrid + cross-encoder pipeline from settings.

    An explicit ``reranker`` (or ``sufficiency_checker``) always wins; otherwise
    a ``ViRankerReranker`` is built when ``use_viranker`` is true and the
    sufficiency gate is calibrated to ``RERANKER__THRESHOLD`` (sigmoid-scale
    cross-encoder scores). With ``IdentityReranker`` the zero-threshold RRF
    baseline is kept. Dense, sparse and expansion share one ``RowProvider``.
    """
    resolved = settings_obj or global_settings
    if provider is None and engine is not None:
        provider = SqlAlchemyRowProvider(engine)
    shared_provider: RowProvider = provider or SqlAlchemyRowProvider()

    if hybrid_service is not None:
        hybrid = hybrid_service
    else:
        embedding = embedding_service or get_shared_embedding_service(resolved)
        hybrid = HybridRetrievalService(
            dense_service=DenseRetrievalService(
                embedding_service=embedding, provider=shared_provider
            ),
            sparse_service=SparseRetrievalService(provider=shared_provider),
            k=rrf_k,
        )

    effective_reranker = (
        reranker if reranker is not None else build_reranker(resolved, use_viranker=use_viranker)
    )
    effective_sufficiency = sufficiency_checker
    if effective_sufficiency is None:
        if isinstance(effective_reranker, ViRankerReranker):
            effective_sufficiency = SufficiencyChecker(
                min_score_threshold=resolved.reranker.threshold
            )
        else:
            effective_sufficiency = SufficiencyChecker(
                min_score_threshold=DEFAULT_MIN_SCORE_THRESHOLD
            )

    effective_synthesizer = synthesizer
    if effective_synthesizer is None and generate is not None:
        effective_synthesizer = PromptAnswerSynthesizer(generate=generate)

    pipeline = RetrievalPipeline(
        hybrid,
        shared_provider,
        reranker=effective_reranker,
        per_document_cap=per_document_cap,
        rerank_top_k_max=rerank_top_k_max,
        sufficiency_checker=effective_sufficiency,
        retry_policy=retry_policy or RetryPolicy(),
        synthesizer=effective_synthesizer,
    )
    logger.info(
        "retrieval_pipeline_built",
        extra={
            "reranker": type(effective_reranker).__name__,
            "rrf_k": rrf_k,
            "rerank_top_k_max": rerank_top_k_max,
            "min_score_threshold": effective_sufficiency._min_score,
        },
    )
    return pipeline
