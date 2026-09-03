"""Bounded retrieval retry (spec P10-17).

Hard cap: ``max_retrieval_attempts = 2`` (spec default).  When the sufficiency
checker yields INSUFFICIENT or PARTIAL, this module produces a modified query
for the second attempt — or ``None`` when retrying cannot help.

No unbounded loop: attempts are counted by the caller (``RetrievalPipeline``).
"""

from __future__ import annotations

import logging
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.retrieval import ExpansionPolicy, RetrievalQuery
from app.domain.models.sufficiency import SufficiencyStatus, SufficiencyVerdict

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 2


class RetryStrategy(StrEnum):
    """Individual retry tactics applied to the query."""

    INCREASE_K = "INCREASE_K"
    RELAX_FILTER = "RELAX_FILTER"
    CHANGE_EXPANSION = "CHANGE_EXPANSION"


class RetryPolicy(BaseModel):
    """Configurable retry parameters."""

    model_config = ConfigDict(frozen=True)

    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1, le=5)
    strategies: list[RetryStrategy] = Field(
        default_factory=lambda: [RetryStrategy.INCREASE_K, RetryStrategy.CHANGE_EXPANSION],
    )


def apply_retry_strategy(
    query: RetrievalQuery,
    verdict: SufficiencyVerdict,
    attempt: int,
    *,
    policy: RetryPolicy | None = None,
) -> RetrievalQuery | None:
    """Produce a modified query for a retry attempt, or ``None`` to stop.

    Returns ``None`` when:
    - The verdict is SUFFICIENT (no retry needed);
    - ``attempt`` has reached the maximum;
    - No applicable strategy can improve the situation.
    """
    effective_policy = policy or RetryPolicy()

    if verdict.status is SufficiencyStatus.SUFFICIENT:
        return None

    if attempt >= effective_policy.max_attempts:
        logger.info(
            "retry_exhausted",
            extra={"attempt": attempt, "max": effective_policy.max_attempts},
        )
        return None

    overrides: dict[str, object] = {}

    for strategy in effective_policy.strategies:
        if strategy is RetryStrategy.INCREASE_K:
            new_dense = min(query.top_k_dense + 10, 100)
            new_sparse = min(query.top_k_sparse + 10, 100)
            if new_dense != query.top_k_dense or new_sparse != query.top_k_sparse:
                overrides["top_k_dense"] = new_dense
                overrides["top_k_sparse"] = new_sparse

        elif strategy is RetryStrategy.RELAX_FILTER:
            if query.source_filters:
                overrides["source_filters"] = {}

        elif strategy is RetryStrategy.CHANGE_EXPANSION:
            if query.expansion_policy is not ExpansionPolicy.PARENT:
                overrides["expansion_policy"] = ExpansionPolicy.PARENT

    if not overrides:
        logger.info("retry_no_applicable_strategy")
        return None

    logger.info(
        "retry_query_modified",
        extra={"attempt": attempt + 1, "overrides": list(overrides.keys())},
    )

    # RetrievalQuery is frozen — rebuild with overrides.
    data = query.model_dump()
    data.update(overrides)
    return RetrievalQuery(**data)
