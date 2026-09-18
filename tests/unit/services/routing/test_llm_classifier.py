"""Unit tests for FastTriage LLM classifier fallback helpers."""

from app.domain.enums import Domain, RouteType
from app.domain.models.routing.route import RouteDecision
from app.services.routing.llm_classifier import (
    decision_from_label,
    needs_llm_fallback,
    parse_classifier_label,
)
from app.services.routing.triage import FastTriage


def test_parse_classifier_label_variants() -> None:
    assert parse_classifier_label("knowledge_research") == "knowledge_research"
    assert parse_classifier_label("Label: calendar") == "calendar"
    assert parse_classifier_label("COMMUNICATION\nextra") == "communication"
    assert parse_classifier_label("research docs") == "knowledge_research"
    assert parse_classifier_label("") is None


def test_decision_from_label_routes_specialists() -> None:
    cal = decision_from_label("lich hop", "calendar")
    assert cal is not None
    assert cal.route_type == RouteType.DIRECT_SPECIALIST
    assert cal.target_agent == "CalendarAgent"
    assert cal.domains == [Domain.CALENDAR]

    multi = decision_from_label("tim tai lieu roi gui mail", "multi")
    assert multi is not None
    assert multi.route_type == RouteType.SUPERVISOR_DAG

    assert decision_from_label("???", "clarify") is None


def test_needs_llm_fallback_for_clarification_and_low_confidence() -> None:
    clarify = RouteDecision(
        route_type=RouteType.CLARIFICATION,
        confidence=0.70,
        reasoning="ambiguous",
        domains=[Domain.GENERAL],
        reason_code="AMBIGUOUS_QUERY_CLARIFICATION",
    )
    assert needs_llm_fallback(clarify) is True

    empty = RouteDecision(
        route_type=RouteType.CLARIFICATION,
        confidence=1.0,
        reasoning="empty",
        domains=[Domain.GENERAL],
        reason_code="EMPTY_INPUT",
    )
    assert needs_llm_fallback(empty) is False

    strong = RouteDecision(
        route_type=RouteType.DIRECT_SPECIALIST,
        target_agent="CalendarAgent",
        confidence=0.95,
        reasoning="match",
        domains=[Domain.CALENDAR],
        reason_code="SINGLE_DOMAIN_CALENDAR",
    )
    assert needs_llm_fallback(strong) is False


def test_triage_invokes_llm_fallback_for_ambiguous_query(monkeypatch) -> None:
    expected = RouteDecision(
        route_type=RouteType.DIRECT_SPECIALIST,
        target_agent="KnowledgeResearchAgent",
        confidence=0.82,
        parameters={"query": "xyzzy foobar"},
        reasoning="LLM classifier routed to KnowledgeResearchAgent.",
        domains=[Domain.KNOWLEDGE_RESEARCH],
        reason_code="LLM_FALLBACK_KNOWLEDGE_RESEARCH",
    )

    monkeypatch.setattr(
        "app.services.routing.triage.classify_with_llm",
        lambda _query: expected,
    )

    decision = FastTriage().triage("xyzzy foobar")
    assert decision.route_type == RouteType.DIRECT_SPECIALIST
    assert decision.target_agent == "KnowledgeResearchAgent"
    assert decision.reason_code == "LLM_FALLBACK_KNOWLEDGE_RESEARCH"
