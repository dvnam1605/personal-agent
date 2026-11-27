"""Backward-compatible import path for the retrieval benchmark corpus.

The corpus now lives in the ``tests.fixtures.retrieval_benchmark`` package; this module only
re-exports it so older imports keep working.
"""

from tests.fixtures.retrieval_benchmark import *  # noqa: F403
from tests.fixtures.retrieval_benchmark import (
    get_benchmark_corpus,
    get_benchmark_queries,
    get_queries_by_category,
    get_query_by_id,
)

__all__ = [
    "get_benchmark_corpus",
    "get_benchmark_queries",
    "get_queries_by_category",
    "get_query_by_id",
]
