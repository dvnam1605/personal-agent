"""Tavily web search adapter implementing the ``WebSearchProvider`` contract."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.settings.web import WebSearchSettings
from app.domain.errors import ExternalServiceError
from app.domain.models.retrieval.web import WebSearchPage, WebSearchResultItem

MAX_QUERY_LEN = 400
MAX_SNIPPET_LEN = 1500


class TavilyWebSearchProvider:
    """Search the web through Tavily and return provenance-labelled, untrusted results.

    Only the question text leaves the machine. Failures surface as
    ``ExternalServiceError`` so the caller can tell the user the search did not run.
    """

    def __init__(self, settings: WebSearchSettings, client: httpx.AsyncClient | None = None):
        if not settings.api_key:
            raise ValueError("Tavily requires WEB_SEARCH__API_KEY.")
        self._settings = settings
        self._client = client

    async def search(self, query: str, *, max_results: int = 5) -> WebSearchPage:
        normalized = " ".join(query.split())[:MAX_QUERY_LEN]
        payload = {
            "query": normalized,
            "max_results": min(max_results, self._settings.max_results),
            "search_depth": self._settings.search_depth,
            "include_answer": False,
        }
        try:
            data = await self._post("/search", payload)
        except (httpx.HTTPError, ValueError) as exc:
            raise ExternalServiceError(f"Web search failed: {type(exc).__name__}") from exc
        items: list[WebSearchResultItem] = []
        for raw in list(data.get("results") or []):
            url = str(raw.get("url") or "").strip()
            if not url.startswith(("https://", "http://")):
                continue
            items.append(
                WebSearchResultItem(
                    url=url,
                    title=str(raw.get("title") or ""),
                    snippet=str(raw.get("content") or "")[:MAX_SNIPPET_LEN],
                    source="tavily",
                )
            )
        return WebSearchPage(items=items, query=normalized)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._settings.api_key}"}
        url = self._settings.base_url.rstrip("/") + path
        if self._client is not None:
            response = await self._client.post(url, json=payload, headers=headers)
        else:
            timeout = httpx.Timeout(self._settings.timeout_seconds, connect=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("Unexpected Tavily response shape.")
        return body
