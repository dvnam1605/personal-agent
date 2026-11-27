"""Best-effort DB reads roll back to a savepoint, so a failure never poisons the request.

On Postgres a failed statement aborts the whole transaction; swallowing the error
without rolling back would make the next query raise ``PendingRollbackError``.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

from sqlalchemy.exc import OperationalError

from app.domain.enums import Complexity, Domain, RouteType
from app.domain.models.routing.route import RouteDecision
from app.services.routing.query import QueryOrchestrator
from app.services.routing.query.followup import FollowupMixin


class _Savepoint:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def __aenter__(self) -> None:
        self.log.append("savepoint")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self.log.append("rollback" if exc_type else "release")
        return False


class FailingSession:
    """Every statement fails; records savepoint use."""

    def __init__(self) -> None:
        self.log: list[str] = []

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self.log)

    async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise OperationalError("SELECT 1", {}, Exception("connection reset"))


async def test_history_read_failure_rolls_back_to_the_savepoint() -> None:
    session = FailingSession()
    history = await FollowupMixin._load_conversation_history(session, "conv", "u1")  # type: ignore[arg-type]
    assert history == []
    assert session.log == ["savepoint", "rollback"]


async def test_followup_scope_failure_rolls_back_to_the_savepoint() -> None:
    session = FailingSession()
    await FollowupMixin._set_followup_scope(session, "conv", "u1", "anchor")  # type: ignore[arg-type]
    assert session.log == ["savepoint", "rollback"]


async def test_document_route_failure_rolls_back_to_the_savepoint(monkeypatch: Any) -> None:
    from app.services.documents.service import DocumentGenerationService

    async def failing_pending(*_args: Any, **_kwargs: Any) -> Any:
        raise OperationalError("SELECT 1", {}, Exception("connection reset"))

    monkeypatch.setattr(DocumentGenerationService, "pending_outline", failing_pending)
    session = FailingSession()
    decision = RouteDecision(
        route_type=RouteType.DIRECT_SPECIALIST,
        confidence=0.9,
        domains=[Domain.GENERAL],
        complexity=Complexity.DIRECT,
    )
    result = await QueryOrchestrator()._maybe_handle_document(
        session,  # type: ignore[arg-type]
        "u1",
        "viết báo cáo",
        "viết báo cáo",
        "run",
        decision,
        "conv",
    )
    assert result is None
    assert session.log == ["savepoint", "rollback"]
