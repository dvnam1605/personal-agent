"""Natural-language query endpoint (triage + live specialist execution)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id
from app.infrastructure.db.session import get_db_session
from app.services.routing.query import QueryOrchestrator, QueryResult

router = APIRouter(tags=["query"])

_orchestrator: QueryOrchestrator | None = None


class QueryRequest(BaseModel):
    """Natural-language question or command."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=4000)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


def get_query_orchestrator() -> QueryOrchestrator:
    """Process-wide orchestrator so the retrieval pipeline is built once."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = QueryOrchestrator()
    return _orchestrator


@router.post(
    "/query",
    response_model=QueryResult,
    summary="Ask a natural-language question (calendar, RAG, or mail read)",
)
async def submit_query(
    body: QueryRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    orchestrator: QueryOrchestrator = Depends(get_query_orchestrator),  # noqa: B008
) -> QueryResult:
    correlation_id = request.headers.get("X-Request-ID")
    return await orchestrator.handle(
        session,
        user_id,
        body.query,
        correlation_id=correlation_id,
    )


__all__ = ["QueryRequest", "get_query_orchestrator", "router"]
