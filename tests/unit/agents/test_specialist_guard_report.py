"""Unit tests for the repeat guard and report channel (spec P11-09/10)."""

from __future__ import annotations

import pytest

from app.agents.specialist.guard import RepeatToolGuard
from app.agents.specialist.report import (
    REPORT_TOOL_NAME,
    ReportValidationError,
    coerce_report_arguments,
    parse_report_call,
    report_from_stop,
)
from app.domain.enums import SpecialistStatus, StopReason
from app.domain.models import ToolCallRequest


class TestRepeatToolGuard:
    def test_first_failure_is_quiet(self) -> None:
        guard = RepeatToolGuard()
        decision = guard.observe("t.search", {"q": "x"}, success=False, error="boom")
        assert decision.reminder is None
        assert decision.tripped is False

    def test_identical_repeat_triggers_reminder(self) -> None:
        guard = RepeatToolGuard(remind_after=2, breaker_after=3)
        guard.observe("t.search", {"q": "x"}, success=False, error="boom")
        decision = guard.observe("t.search", {"q": "x"}, success=False, error="boom")
        assert decision.reminder is not None
        assert "t.search" in decision.reminder
        assert "2 times" in decision.reminder
        assert decision.tripped is False

    def test_breaker_trips_on_threshold(self) -> None:
        guard = RepeatToolGuard(remind_after=2, breaker_after=3)
        for _ in range(2):
            guard.observe("t.search", {"q": "x"}, success=False, error="boom")
        decision = guard.observe("t.search", {"q": "x"}, success=False, error="boom")
        assert decision.tripped is True
        assert guard.tripped is True

    def test_empty_successful_output_counts_as_unproductive(self) -> None:
        guard = RepeatToolGuard(remind_after=2, breaker_after=9)
        guard.observe("t.search", {}, success=True, output="")
        decision = guard.observe("t.search", {}, success=True, output=[])
        assert decision.reminder is not None

    def test_success_resets_streak(self) -> None:
        guard = RepeatToolGuard(remind_after=2, breaker_after=9)
        guard.observe("t.search", {"q": "x"}, success=False, error="boom")
        guard.observe("t.search", {"q": "x"}, success=True, output="data")
        decision = guard.observe("t.search", {"q": "x"}, success=False, error="boom")
        assert decision.reminder is None
        assert decision.repeat_count == 1

    def test_different_arguments_reset_streak(self) -> None:
        guard = RepeatToolGuard(remind_after=2, breaker_after=9)
        guard.observe("t.search", {"q": "x"}, success=False, error="boom")
        decision = guard.observe("t.search", {"q": "y"}, success=False, error="boom")
        assert decision.reminder is None

    def test_invalid_thresholds_rejected(self) -> None:
        with pytest.raises(ValueError):
            RepeatToolGuard(remind_after=0)
        with pytest.raises(ValueError):
            RepeatToolGuard(remind_after=3, breaker_after=2)


class TestReportChannel:
    def test_valid_report_parses(self) -> None:
        call = ToolCallRequest(
            tool_name=REPORT_TOOL_NAME,
            arguments={"status": "success", "summary": "done", "data": {"n": 1}},
        )
        report = parse_report_call(call)
        assert report.status is SpecialistStatus.SUCCESS
        assert report.data == {"n": 1}

    def test_wrong_tool_rejected(self) -> None:
        call = ToolCallRequest(tool_name="other.tool", arguments={})
        with pytest.raises(ReportValidationError, match="Not a report call"):
            parse_report_call(call)

    def test_invalid_payload_rejected(self) -> None:
        call = ToolCallRequest(tool_name=REPORT_TOOL_NAME, arguments={"status": "bogus"})
        with pytest.raises(ReportValidationError, match="Invalid report payload"):
            parse_report_call(call)

    def test_coerce_normalizes_status_case(self) -> None:
        call = ToolCallRequest(
            tool_name=REPORT_TOOL_NAME,
            arguments=coerce_report_arguments({"status": "SUCCESS", "summary": "ok"}),
        )
        assert parse_report_call(call).status is SpecialistStatus.SUCCESS

    def test_report_from_stop(self) -> None:
        assert report_from_stop(StopReason.SUCCESS, "ok").status is SpecialistStatus.SUCCESS
        blocked = report_from_stop(StopReason.MAX_STEPS, "stuck")
        assert blocked.status is SpecialistStatus.BLOCKED
        assert blocked.blockers == ["stuck"]
