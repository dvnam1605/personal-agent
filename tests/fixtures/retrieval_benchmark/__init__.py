"""Curated retrieval benchmark corpus (spec P10-21). Kept out of app/ production imports.

``ids`` holds the deterministic document/chunk UUIDs, ``documents`` and ``chunks`` the corpus
content, ``corpus`` its assembly and ``queries`` the ground-truth queries; this package re-exports
them and adds small accessors.
"""

from __future__ import annotations

from tests.fixtures.retrieval_benchmark.chunks import *  # noqa: F403
from tests.fixtures.retrieval_benchmark.corpus import BENCHMARK_CORPUS
from tests.fixtures.retrieval_benchmark.documents import *  # noqa: F403
from tests.fixtures.retrieval_benchmark.ids import *  # noqa: F403
from tests.fixtures.retrieval_benchmark.queries import BENCHMARK_QUERIES
from tests.fixtures.retrieval_benchmark_types import (
    BenchmarkCorpus,
    BenchmarkQuery,
    BenchmarkQueryCategory,
)


def get_benchmark_corpus() -> BenchmarkCorpus:
    """Return the normative benchmark corpus."""
    return BENCHMARK_CORPUS


def get_benchmark_queries() -> list[BenchmarkQuery]:
    """Return all benchmark queries."""
    return list(BENCHMARK_QUERIES)


def get_queries_by_category(category: BenchmarkQueryCategory) -> list[BenchmarkQuery]:
    """Filter benchmark queries by category."""
    return [q for q in BENCHMARK_QUERIES if q.category == category]


def get_query_by_id(query_id: str) -> BenchmarkQuery | None:
    """Look up a benchmark query by ID."""
    for q in BENCHMARK_QUERIES:
        if q.query_id == query_id:
            return q
    return None
