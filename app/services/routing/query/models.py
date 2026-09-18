"""Data models and type definitions for query orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.routing.triage import RouteDecision

GOOGLE_CONNECT_HINT = (
    "Google chưa được kết nối hoặc token không còn hiệu lực. Mở GET /auth/google/start rồi thử lại."
)


class QueryRouteInfo(BaseModel):
    """Triage metadata returned with every `/query` response."""

    model_config = ConfigDict(extra="forbid")

    route_type: str
    target_agent: str | None = None
    target_workflow_id: str | None = None
    reason_code: str | None = None
    domains: list[str] = Field(default_factory=list)
    confidence: float


def route_info(decision: RouteDecision) -> QueryRouteInfo:
    """Project a RouteDecision into the HTTP response shape."""
    return QueryRouteInfo(
        route_type=decision.route_type.value,
        target_agent=decision.target_agent,
        target_workflow_id=decision.target_workflow_id,
        reason_code=decision.reason_code,
        domains=[domain.value for domain in decision.domains],
        confidence=decision.confidence,
    )


class QueryResult(BaseModel):
    """Stable JSON body for `POST /query`."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: str
    message: str
    route: QueryRouteInfo
    data: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None


CalendarFactory = Callable[[AsyncSession, str], Awaitable[Any]]
CommunicationFactory = Callable[[AsyncSession, str], Awaitable[Any]]
RetrieveFn = Callable[[str, str], Awaitable[Any]]
ClockFn = Callable[[], datetime]
RunIdFn = Callable[[], str]
SummarizeEmailsFn = Callable[[str, list[dict[str, Any]]], Awaitable[str]]

__all__ = [
    "CalendarFactory",
    "ClockFn",
    "CommunicationFactory",
    "GOOGLE_CONNECT_HINT",
    "QueryResult",
    "QueryRouteInfo",
    "RetrieveFn",
    "RunIdFn",
    "SummarizeEmailsFn",
    "route_info",
]
