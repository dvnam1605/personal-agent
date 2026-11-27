"""Domain detection and fallbacks of the multi-domain supervisor DAG."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.routing.live_workflows import _dag_flags, run_supervisor_dag
from app.services.routing.query import QueryRouteInfo


def _route(*domains: str) -> QueryRouteInfo:
    return QueryRouteInfo(route_type="supervisor", domains=list(domains), confidence=0.9)


@pytest.mark.parametrize(
    "query",
    ["Tìm phương án phù hợp nhất", "Tổng hợp lịch sử dự án", "Viết lịch sự hơn"],
)
def test_substrings_do_not_fire_the_calendar_step(query: str) -> None:
    assert not _dag_flags(query, _route()).calendar


@pytest.mark.parametrize("query", ["Lịch họp ngày mai", "Có cuộc họp nào không", "xem lich"])
def test_calendar_words_fire_the_calendar_step(query: str) -> None:
    assert _dag_flags(query, _route()).calendar


async def _no_call(*_: Any, **__: Any) -> Any:
    raise AssertionError("must not be called")


async def _run(query: str, route: QueryRouteInfo) -> Any:
    return await run_supervisor_dag(
        session=SimpleNamespace(),  # type: ignore[arg-type]
        user_id="u1",
        query=query,
        run_id="r1",
        route=route,
        now=datetime(2026, 11, 27, tzinfo=UTC),
        list_events=_no_call,
        search_messages=_no_call,
        retrieve=_no_call,
    )


async def test_mail_request_without_draft_is_reported_not_dropped() -> None:
    result = await _run("Kiểm tra email mới", _route("communication"))
    assert result.status == "completed"
    assert "email" in result.message
    assert "chưa được xử lý" in result.message


async def test_no_domain_gives_a_useful_answer() -> None:
    result = await _run("Giúp tôi một việc phù hợp", _route())
    assert "Hãy nói rõ hơn" in result.message
