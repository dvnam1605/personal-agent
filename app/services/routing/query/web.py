"""Explicit web search in the query orchestrator ("tìm trên web …")."""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.domain.enums import RouteType
from app.domain.errors import ExternalServiceError
from app.domain.models.routing.route import RouteDecision
from app.integrations.tavily import TavilyWebSearchProvider
from app.services.web import WebAnswerService, parse_web_request, sources_markdown

from .memory import MemoryMixin
from .models import QueryResult, route_info

WEB_UNAVAILABLE = (
    "Tìm kiếm web chưa được cấu hình (thiếu WEB_SEARCH__API_KEY). "
    "Tôi vẫn trả lời được từ tài liệu nội bộ, lịch và email."
)


class WebMixin(MemoryMixin):
    """Answer from the web only when the user asks for it in so many words.

    The question goes to the provider as typed; nothing from internal documents, mail or
    memory is added to it. Results reach the model inside the untrusted web boundary and
    the answer lists the pages it cites.
    """

    def _web_service(self) -> WebAnswerService | None:
        if self._web_answer_service is not None:
            return self._web_answer_service
        if not settings.web_search.api_key:
            return None
        provider = TavilyWebSearchProvider(settings.web_search)
        return WebAnswerService(provider, max_results=settings.web_search.max_results)

    async def _maybe_handle_web(
        self, query: str, run_id: str, decision: RouteDecision
    ) -> QueryResult | None:
        """Answer an explicit web request; ``None`` when the turn is not one."""
        if decision.route_type is RouteType.REJECT:
            return None
        question = parse_web_request(query)
        if question is None:
            return None
        route = route_info(decision)
        service = self._web_service()
        if service is None:
            return QueryResult(
                run_id=run_id, status="blocked", message=WEB_UNAVAILABLE, route=route
            )
        try:
            answer = await service.answer(question)
        except ExternalServiceError:
            return QueryResult(
                run_id=run_id,
                status="blocked",
                message="Không tìm được trên web lúc này. Bạn thử lại sau ít phút.",
                route=route,
            )
        data: dict[str, Any] = {
            "web_query": question,
            "web_sources": [vars(source) for source in answer.sources],
        }
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=answer.answer + "\n" + sources_markdown(answer.sources),
            route=route,
            data=data,
        )
