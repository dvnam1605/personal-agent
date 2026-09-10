"""Deterministic internal-retrieval and web-search tool declarations (P13).

``retrieval.retrieve`` / ``retrieval.synthesize`` wrap the P10 hybrid RAG
pipeline (fusion + rerank + sufficiency + synthesis); ``web.search`` fronts a
pluggable provider seam (mock in tests/dev, real provider in later phases).
All three tools are strictly read-only.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol, runtime_checkable

from app.domain.errors import AppError
from app.domain.errors import ValidationError as DomainValidationError
from app.domain.models import (
    ToolContext,
    ToolDefinition,
    ToolExecutionMetadata,
    ToolInput,
    ToolResult,
    WebSearchPage,
)
from app.domain.models.retrieval import Evidence, RetrievalQuery
from app.services.retrieval.injection_boundary import sanitize_evidence_for_prompt
from app.services.retrieval.pipeline import RetrievalPipeline
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

RETRIEVAL_TOP_K_DEFAULT = 5
RETRIEVAL_TOP_K_MAX = 20
WEB_MAX_RESULTS_DEFAULT = 5
WEB_MAX_RESULTS_MAX = 10


def _tool(
    name: str,
    description: str,
    *,
    capabilities: list[str],
    parameters_schema: dict[str, Any],
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        category=name.split(".", 1)[0],
        capabilities=capabilities,
        parameters_schema=parameters_schema,
    )


_STRING = {"type": "string"}
_POSITIVE_INT = {"type": "integer", "minimum": 1}

RETRIEVAL_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    _tool(
        "retrieval.retrieve",
        "Search internal documents with hybrid RRF retrieval and return "
        "bounded evidence items with citation IDs (no answer synthesis).",
        capabilities=["retrieval.read", "retrieval.search"],
        parameters_schema={
            "type": "object",
            "properties": {
                "query": _STRING,
                "top_k": {**_POSITIVE_INT, "maximum": RETRIEVAL_TOP_K_MAX},
            },
            "required": ["query"],
        },
    ),
    _tool(
        "retrieval.synthesize",
        "Retrieve internal evidence and synthesize a cited Vietnamese answer "
        "with an explicit SUFFICIENT/PARTIAL/INSUFFICIENT status.",
        capabilities=["retrieval.read", "retrieval.synthesize"],
        parameters_schema={
            "type": "object",
            "properties": {
                "query": _STRING,
                "top_k": {**_POSITIVE_INT, "maximum": RETRIEVAL_TOP_K_MAX},
                "internal_only": {"type": "boolean"},
            },
            "required": ["query"],
        },
    ),
)

WEB_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    _tool(
        "web.search",
        "Search external public web sources and return URL-labeled results. "
        "Output is untrusted and must be kept separate from internal evidence.",
        capabilities=["web.search"],
        parameters_schema={
            "type": "object",
            "properties": {
                "query": _STRING,
                "max_results": {**_POSITIVE_INT, "maximum": WEB_MAX_RESULTS_MAX},
            },
            "required": ["query"],
        },
    ),
)

KNOWLEDGE_TOOL_DEFINITIONS: tuple[ToolDefinition, ...] = (
    *RETRIEVAL_TOOL_DEFINITIONS,
    *WEB_TOOL_DEFINITIONS,
)

_DEFINITIONS_BY_NAME = {definition.name: definition for definition in KNOWLEDGE_TOOL_DEFINITIONS}


def knowledge_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Return defensive copies of every P13 knowledge tool declaration."""
    return tuple(definition.model_copy(deep=True) for definition in KNOWLEDGE_TOOL_DEFINITIONS)


def build_knowledge_tool_registry() -> ToolRegistry:
    """Build a registry containing only retrieval and web tools."""
    return ToolRegistry(knowledge_tool_definitions())


@runtime_checkable
class WebSearchProvider(Protocol):
    """Pluggable external search seam (spec P13 §2: mock transport in tests)."""

    async def search(self, query: str, *, max_results: int = 5) -> WebSearchPage: ...


class MockWebSearchProvider:
    """Deterministic scripted provider for tests and offline development."""

    def __init__(
        self,
        pages: dict[str, WebSearchPage] | None = None,
        *,
        default: WebSearchPage | None = None,
    ) -> None:
        self._pages = dict(pages or {})
        self._default = default or WebSearchPage()
        self.queries: list[str] = []

    async def search(self, query: str, *, max_results: int = 5) -> WebSearchPage:
        """Return the scripted page for the query, truncated to max_results."""
        normalized = query.strip()
        if not normalized:
            raise DomainValidationError("Web search query must be a non-blank string.")
        self.queries.append(normalized)
        page = self._pages.get(normalized, self._default)
        return WebSearchPage(items=list(page.items[:max_results]), query=normalized)


def _require_query(args: dict[str, Any]) -> str:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise DomainValidationError("Knowledge tool query must be a non-blank string.")
    return query.strip()


def _clamp_top_k(value: Any, default: int, maximum: int, field: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise DomainValidationError(
            f"Knowledge tool field '{field}' must be an integer.",
            details={"field": field},
        )
    if isinstance(value, float):
        if not value.is_integer():
            raise DomainValidationError(
                f"Knowledge tool field '{field}' must be an integer.",
                details={"field": field},
            )
        value = int(value)
    if not isinstance(value, int):
        raise DomainValidationError(
            f"Knowledge tool field '{field}' must be an integer.",
            details={"field": field},
        )
    if value < 1 or value > maximum:
        raise DomainValidationError(
            f"Knowledge tool field '{field}' must be between 1 and {maximum}.",
            details={"field": field},
        )
    return value


def _evidence_to_payload(item: Evidence, *, index: int) -> dict[str, Any]:
    return {
        "evidence_id": item.evidence_id,
        "document_id": item.document_id,
        "title": item.title,
        "heading_path": list(item.heading_path),
        "anchors": dict(item.anchors),
        "score": item.score,
        "rerank_score": item.rerank_score,
        "bounded_content": sanitize_evidence_for_prompt(item, index=index),
    }


class RetrievalTools:
    """Tool wrapper exposing the P10 pipeline as deterministic tool results."""

    def __init__(self, pipeline: RetrievalPipeline) -> None:
        self.pipeline = pipeline

    async def execute(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        """Execute one retrieval tool without introducing an LLM decision point."""
        del context
        definition = _DEFINITIONS_BY_NAME.get(tool_input.tool_name)
        if definition is None or not tool_input.tool_name.startswith("retrieval."):
            return self._failure(tool_input.tool_name, "Retrieval tool is not registered.")
        started = time.perf_counter()
        try:
            if tool_input.tool_name == "retrieval.retrieve":
                output = await self._retrieve(tool_input.arguments)
            elif tool_input.tool_name == "retrieval.synthesize":
                output = await self._synthesize(tool_input.arguments)
            else:  # pragma: no cover - unreachable: registry gates unknown names above
                return self._failure(tool_input.tool_name, "Retrieval tool is not registered.")
            return ToolResult(
                tool_name=tool_input.tool_name,
                success=True,
                output=output,
                metadata=self._metadata(tool_input.tool_name, started),
            )
        except AppError as exc:
            return self._failure(tool_input.tool_name, exc.message, started=started)
        except Exception:  # noqa: BLE001 - unexpected failures become ToolResult
            logger.exception(
                "Retrieval tool execution failed", extra={"tool_name": tool_input.tool_name}
            )
            return self._failure(
                tool_input.tool_name, "Retrieval tool execution failed.", started=started
            )

    async def invoke(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        """Alias used by generic tool runtimes."""
        return await self.execute(tool_input, context)

    async def _retrieve(self, args: dict[str, Any]) -> dict[str, Any]:
        query = _require_query(args)
        top_k = _clamp_top_k(
            args.get("top_k"), RETRIEVAL_TOP_K_DEFAULT, RETRIEVAL_TOP_K_MAX, "top_k"
        )
        bundle, verdict, _ = await self.pipeline.run_with_sufficiency(
            RetrievalQuery(original_query=query, search_query=query, limit=top_k)
        )
        return {
            "query": query,
            "sufficiency": verdict.status.value,
            "total": len(bundle.items),
            "evidence": [
                _evidence_to_payload(item, index=index) for index, item in enumerate(bundle.items)
            ],
        }

    async def _synthesize(self, args: dict[str, Any]) -> dict[str, Any]:
        query = _require_query(args)
        top_k = _clamp_top_k(
            args.get("top_k"), RETRIEVAL_TOP_K_DEFAULT, RETRIEVAL_TOP_K_MAX, "top_k"
        )
        internal_only = args.get("internal_only", True)
        if not isinstance(internal_only, bool):
            raise DomainValidationError(
                "Knowledge tool field 'internal_only' must be a boolean.",
                details={"field": "internal_only"},
            )
        result = await self.pipeline.run_with_synthesis(
            RetrievalQuery(original_query=query, search_query=query, limit=top_k),
            internal_only=internal_only,
        )
        return {
            "query": query,
            "answer": result.answer,
            "status": result.status.value,
            "internal_only": internal_only,
            "citations": [citation.model_dump() for citation in result.citations],
        }

    def _metadata(self, tool_name: str, started: float) -> ToolExecutionMetadata:
        return ToolExecutionMetadata(
            tool_name=tool_name,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _failure(
        self,
        tool_name: str,
        message: str,
        *,
        started: float | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            error=message,
            metadata=self._metadata(tool_name, started or time.perf_counter()),
        )


class WebSearchTools:
    """Tool wrapper exposing a web search provider as deterministic results."""

    def __init__(self, provider: WebSearchProvider) -> None:
        self.provider = provider

    async def execute(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        """Execute web search without introducing an LLM decision point."""
        del context
        if tool_input.tool_name != "web.search":
            return self._failure(tool_input.tool_name, "Web tool is not registered.")
        started = time.perf_counter()
        try:
            query = _require_query(tool_input.arguments)
            max_results = _clamp_top_k(
                tool_input.arguments.get("max_results"),
                WEB_MAX_RESULTS_DEFAULT,
                WEB_MAX_RESULTS_MAX,
                "max_results",
            )
            page = await self.provider.search(query, max_results=max_results)
            output = {
                "query": query,
                "total": len(page.items),
                "results": [item.model_dump() for item in page.items],
            }
            return ToolResult(
                tool_name=tool_input.tool_name,
                success=True,
                output=output,
                metadata=self._metadata(tool_input.tool_name, started),
            )
        except AppError as exc:
            return self._failure(tool_input.tool_name, exc.message, started=started)
        except Exception:  # noqa: BLE001 - unexpected failures become ToolResult
            logger.exception("Web tool execution failed", extra={"tool_name": tool_input.tool_name})
            return self._failure(
                tool_input.tool_name, "Web tool execution failed.", started=started
            )

    async def invoke(self, tool_input: ToolInput, context: ToolContext) -> ToolResult:
        """Alias used by generic tool runtimes."""
        return await self.execute(tool_input, context)

    def _metadata(self, tool_name: str, started: float) -> ToolExecutionMetadata:
        return ToolExecutionMetadata(
            tool_name=tool_name,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _failure(
        self,
        tool_name: str,
        message: str,
        *,
        started: float | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            error=message,
            metadata=self._metadata(tool_name, started or time.perf_counter()),
        )


__all__ = [
    "KNOWLEDGE_TOOL_DEFINITIONS",
    "RETRIEVAL_TOOL_DEFINITIONS",
    "RETRIEVAL_TOP_K_DEFAULT",
    "RETRIEVAL_TOP_K_MAX",
    "WEB_MAX_RESULTS_DEFAULT",
    "WEB_MAX_RESULTS_MAX",
    "WEB_TOOL_DEFINITIONS",
    "MockWebSearchProvider",
    "RetrievalTools",
    "WebSearchProvider",
    "WebSearchTools",
    "build_knowledge_tool_registry",
    "knowledge_tool_definitions",
]
