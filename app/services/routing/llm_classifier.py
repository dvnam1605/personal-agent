"""Lightweight LLM domain classifier used as FastTriage fallback."""

from __future__ import annotations

import re
from typing import Any

import httpx
import structlog

from app.domain.enums import Complexity, Domain, RouteType
from app.domain.models.routing.route import RouteDecision

logger = structlog.get_logger(__name__)

_CONFIDENCE_THRESHOLD = 0.8
_MAX_TOKENS = 48
_TIMEOUT_SECONDS = 8.0

_SYSTEM_PROMPT = (
    "Classify the user request into exactly one domain label.\n"
    "Labels: calendar | communication | knowledge_research | multi | clarify | casual\n"
    "Rules:\n"
    "- calendar: schedules, meetings, availability, reminders\n"
    "- communication: email, messages, drafts, replies\n"
    "- knowledge_research: internal docs, decisions, policies, lookups\n"
    "- multi: needs 2+ of the domains above\n"
    "- casual: greeting/chitchat\n"
    "- clarify: truly ambiguous\n"
    "Reply with ONLY the label."
)

_AGENT_BY_DOMAIN: dict[Domain, str] = {
    Domain.CALENDAR: "CalendarAgent",
    Domain.COMMUNICATION: "CommunicationAgent",
    Domain.KNOWLEDGE_RESEARCH: "KnowledgeResearchAgent",
}

_LABEL_RE = re.compile(
    r"\b(calendar|communication|knowledge_research|knowledge|research|multi|"
    r"clarify|clarification|casual|greeting)\b",
    re.IGNORECASE,
)


def needs_llm_fallback(decision: RouteDecision) -> bool:
    """True when deterministic triage should ask a short LLM classifier."""
    if decision.route_type in {RouteType.REJECT, RouteType.CASUAL_RESPONSE}:
        return False
    if decision.route_type == RouteType.CLARIFICATION:
        # Empty/malformed inputs stay clarification; do not spend LLM tokens.
        if decision.reason_code in {"EMPTY_INPUT", "MALFORMED_INPUT"}:
            return False
        return True
    return decision.confidence < _CONFIDENCE_THRESHOLD


def parse_classifier_label(raw: str) -> str | None:
    """Extract the first recognized domain label from free-form model text."""
    text = (raw or "").strip().lower()
    if not text:
        return None
    # Prefer first line / first token when model is concise.
    first_line = text.splitlines()[0].strip().strip("`\"'.,;:")
    match = _LABEL_RE.search(first_line) or _LABEL_RE.search(text)
    if match is None:
        return None
    label = match.group(1).lower()
    if label in {"knowledge", "research"}:
        return "knowledge_research"
    if label in {"clarification"}:
        return "clarify"
    if label in {"greeting"}:
        return "casual"
    return label


def decision_from_label(query: str, label: str) -> RouteDecision | None:
    """Map a classifier label to a RouteDecision, or None if still ambiguous."""
    if label == "casual":
        return RouteDecision(
            route_type=RouteType.CASUAL_RESPONSE,
            confidence=0.82,
            reasoning="LLM classifier labeled request as casual conversation.",
            domains=[Domain.GENERAL],
            complexity=Complexity.DIRECT,
            reason_code="LLM_FALLBACK_CASUAL",
        )
    if label == "clarify":
        return None
    if label == "multi":
        return RouteDecision(
            route_type=RouteType.SUPERVISOR_DAG,
            confidence=0.82,
            parameters={"query": query},
            reasoning="LLM classifier detected multi-domain intent.",
            domains=[Domain.GENERAL],
            complexity=Complexity.MULTI_STEP,
            reason_code="LLM_FALLBACK_MULTI",
        )
    domain_map = {
        "calendar": Domain.CALENDAR,
        "communication": Domain.COMMUNICATION,
        "knowledge_research": Domain.KNOWLEDGE_RESEARCH,
    }
    domain = domain_map.get(label)
    if domain is None:
        return None
    agent = _AGENT_BY_DOMAIN[domain]
    return RouteDecision(
        route_type=RouteType.DIRECT_SPECIALIST,
        target_agent=agent,
        confidence=0.82,
        parameters={"query": query},
        reasoning=f"LLM classifier routed to {agent}.",
        domains=[domain],
        complexity=Complexity.DIRECT,
        reason_code=f"LLM_FALLBACK_{domain.value.upper()}",
    )


def _extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    # Some router models put text under alternative keys.
    for key in ("reasoning_content", "reasoning", "text"):
        alt = message.get(key)
        if isinstance(alt, str) and alt.strip():
            return alt
    return ""


def classify_with_llm(query: str) -> RouteDecision | None:
    """Call the configured OpenAI-compatible endpoint; return None on failure."""
    try:
        from app.core.config import settings

        api_key = (settings.llm.openai_api_key or "").strip()
        if not api_key:
            logger.warning("llm_classifier_skipped", reason="missing_api_key")
            return None

        url = settings.llm.chat_completions_url()
        model = settings.llm.fast_model or settings.llm.primary_model
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            "temperature": 0.0,
            "max_tokens": _MAX_TOKENS,
            "stream": False,
        }
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            resp = client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        raw = _extract_message_content(data if isinstance(data, dict) else {})
        label = parse_classifier_label(raw)
        if not label:
            logger.warning(
                "llm_classifier_unparsed",
                raw_preview=(raw or "")[:120],
                usage=(data.get("usage") if isinstance(data, dict) else None),
            )
            return None
        decision = decision_from_label(query, label)
        logger.info(
            "llm_classifier_decision",
            label=label,
            route_type=None if decision is None else decision.route_type.value,
            reason_code=None if decision is None else decision.reason_code,
        )
        return decision
    except Exception as exc:  # noqa: BLE001 - fallback must never break triage
        logger.warning("llm_classifier_error", error=str(exc))
        return None


def build_default_llm_classifier():
    """Return a sync classifier callback compatible with FastTriage."""

    def _classifier(query: str) -> RouteDecision:
        decision = classify_with_llm(query)
        if decision is not None:
            return decision
        # FastTriage expects a RouteDecision from the pluggable classifier;
        # returning clarification keeps the existing fallback semantics.
        return RouteDecision(
            route_type=RouteType.CLARIFICATION,
            confidence=0.70,
            reasoning="LLM classifier unavailable or ambiguous; request clarification.",
            domains=[Domain.GENERAL],
            complexity=Complexity.DIRECT,
            reason_code="LLM_FALLBACK_CLARIFICATION",
        )

    return _classifier


__all__ = [
    "build_default_llm_classifier",
    "classify_with_llm",
    "decision_from_label",
    "needs_llm_fallback",
    "parse_classifier_label",
]
