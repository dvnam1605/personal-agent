"""Fast Triage engine for deterministic routing and supervisor bypass (P15 / H3 / M1 / M9).

Implements the Hard Invariant (§6.1):
Single-domain queries (e.g. "Lịch ngày mai?", "Email của Nam?", "Tìm quyết định 123")
MUST NOT invoke the Supervisor LLM. FastTriage resolves >= 70% of user queries
deterministically in <= 10ms with 0 Supervisor LLM tokens.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

import structlog

from app.domain.enums import Complexity, Domain, RouteType
from app.domain.models.routing.route import RouteDecision
from app.services.routing.llm_classifier import classify_with_llm, needs_llm_fallback
from app.services.routing.triage_rules import (
    CALENDAR_CORE,
    CALENDAR_INQUIRY,
    CALENDAR_RELATIVE,
    CALENDAR_WEDNESDAY,
    CALENDAR_WEEKDAY,
    CASUAL_PATTERN,
    COMMUNICATION_PATTERN,
    DEFINITIONAL_INQUIRY_PATTERN,
    DESTRUCTIVE_COMMAND_PATTERN,
    DOC_DURATION_PATTERN,
    DOC_READ_PATTERN,
    DOC_TITLE_CALENDAR_NOISE_PATTERN,
    FOLLOWUP_AFTER_MEETING,
    INVITATION_PATTERN,
    NON_CALENDAR_KE_HOACH,
    NON_MEETING_HOP,
    ORDER_PHRASE,
    PROMPT_ATTACK_PATTERN,
    RESEARCH_DOC_PATTERN,
    RESEARCH_LOOKUP_PATTERN,
    STAGE2_BOOKING,
    STAGE2_LOOKUP,
    STAGE2_MESSAGING,
    TIM_PREFIX_PATTERN,
    TIME_SIGNAL_TIGHT,
)
from app.services.routing.workflow_registry import (
    StaticWorkflowRegistry,
    load_default_workflow_registry,
)
from app.services.skills.matching import match_trigger, unaccent_vietnamese
from app.services.skills.registry import SkillRegistry, load_production_skills

logger = structlog.get_logger(__name__)

# Multi-step conjunctions indicating genuine supervisor decomposition needs
_MULTI_STEP_CONJUNCTIONS = re.compile(
    r"\b(roi|sau do|va sau do|dong thoi|ket hop|lien ket|sau khi|and then|then)\b",
    re.IGNORECASE,
)


def _is_meeting_only_calendar(unaccented_cal: str) -> bool:
    """True when no calendar signal survives after removing title noise/durations.

    Used by the document-topic guard: "cuộc họp"/"kế hoạch" inside a document
    title and deadline phrases like "trước 24 giờ" must not count as calendar
    evidence.
    """
    cleaned = DOC_DURATION_PATTERN.sub("", DOC_TITLE_CALENDAR_NOISE_PATTERN.sub("", unaccented_cal))
    return not (
        CALENDAR_CORE.search(cleaned)
        or CALENDAR_INQUIRY.search(cleaned)
        or CALENDAR_RELATIVE.search(cleaned)
        or CALENDAR_WEEKDAY.search(cleaned)
        or CALENDAR_WEDNESDAY.search(cleaned)
        or TIME_SIGNAL_TIGHT.search(cleaned)
    )


class FastTriage:
    """Deterministic-first classifier routing user requests with zero Supervisor overhead."""

    def __init__(
        self,
        workflow_registry: StaticWorkflowRegistry | None = None,
        skill_registry: SkillRegistry | None = None,
        classifier: Callable[[str], RouteDecision] | None = None,
    ) -> None:
        self._workflows = workflow_registry or load_default_workflow_registry()
        self._skills = skill_registry or load_production_skills()
        self._classifier = classifier

    def triage(self, query: str) -> RouteDecision:
        """Route query directly to specialist, static workflow, or supervisor."""
        started = time.perf_counter()

        # Handle non-string / None defensively
        if not isinstance(query, str):
            decision = RouteDecision(
                route_type=RouteType.CLARIFICATION,
                confidence=1.0,
                reasoning="Malformed non-string query requires user clarification.",
                domains=[Domain.GENERAL],
                complexity=Complexity.DIRECT,
                reason_code="MALFORMED_INPUT",
            )
            self._log_route(decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
            return decision

        normalized = query.strip()

        # 1. Circuit breaker: Empty or whitespace input -> CLARIFICATION
        if not normalized:
            decision = RouteDecision(
                route_type=RouteType.CLARIFICATION,
                confidence=1.0,
                reasoning="Empty or whitespace query requires user clarification.",
                domains=[Domain.GENERAL],
                complexity=Complexity.DIRECT,
                reason_code="EMPTY_INPUT",
            )
            self._log_route(decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
            return decision

        # M9 Optimization: cache unaccented representation once for the entire triage call
        unaccented = unaccent_vietnamese(normalized)

        # 2. Strict Safety Filter (bilingual prompt injection & destructive commands)
        if PROMPT_ATTACK_PATTERN.search(unaccented):
            decision = RouteDecision(
                route_type=RouteType.REJECT,
                confidence=1.0,
                reasoning="Request matches safety boundary rejection rule (prompt attack/jailbreak).",
                domains=[Domain.GENERAL],
                complexity=Complexity.DIRECT,
                reason_code="SAFETY_REJECT",
            )
            self._log_route(decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
            return decision

        if DESTRUCTIVE_COMMAND_PATTERN.search(unaccented):
            if not DEFINITIONAL_INQUIRY_PATTERN.search(unaccented):
                decision = RouteDecision(
                    route_type=RouteType.REJECT,
                    confidence=1.0,
                    reasoning="Request matches safety boundary rejection rule (destructive command).",
                    domains=[Domain.GENERAL],
                    complexity=Complexity.DIRECT,
                    reason_code="SAFETY_REJECT",
                )
                self._log_route(decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
                return decision

        # Calendar domain detection
        unaccented_cal = NON_CALENDAR_KE_HOACH.sub("", NON_MEETING_HOP.sub("", unaccented))
        has_calendar_core = bool(CALENDAR_CORE.search(unaccented_cal))
        has_cal_inquiry = bool(CALENDAR_INQUIRY.search(unaccented))
        has_cal_relative = bool(CALENDAR_RELATIVE.search(unaccented))
        has_time_signal = bool(TIME_SIGNAL_TIGHT.search(unaccented))
        has_calendar_signal = (
            has_calendar_core or has_cal_inquiry or has_cal_relative or has_time_signal
        )

        # Order phrase guard (H1): 'thứ tự' must not be confused with Wednesday 'thứ tư'
        is_order_phrase = bool(ORDER_PHRASE.search(unaccented))
        has_wednesday = (
            bool(CALENDAR_WEDNESDAY.search(unaccented))
            and not is_order_phrase
            and has_calendar_signal
        )
        has_standard_weekday = bool(CALENDAR_WEEKDAY.search(unaccented)) and has_calendar_signal
        has_weekday = has_standard_weekday or has_wednesday

        # Relative date alone is calendar ONLY if paired with calendar core, inquiry, or tight time signal
        has_cal_rel_valid = has_cal_relative and (
            has_calendar_core or has_cal_inquiry or has_time_signal
        )
        has_cal_inquiry_with_time = has_cal_inquiry and (has_time_signal or has_cal_relative)
        has_calendar = (
            has_calendar_core or has_weekday or has_cal_rel_valid or has_cal_inquiry_with_time
        )

        # Communication domain detection
        is_meeting_followup = bool(FOLLOWUP_AFTER_MEETING.search(unaccented))
        is_invitation_comm = bool(INVITATION_PATTERN.search(unaccented))
        has_comm = (
            bool(COMMUNICATION_PATTERN.search(unaccented))
            or is_meeting_followup
            or is_invitation_comm
        )

        # If it is a personal follow-up email/message after a meeting or invitation letter:
        # The meeting/event is only temporal/thematic context, active domain is Communication
        if is_meeting_followup or is_invitation_comm:
            has_calendar = False

        # Research domain detection
        has_doc_terms = bool(RESEARCH_DOC_PATTERN.search(unaccented))
        has_lookup_verbs = bool(RESEARCH_LOOKUP_PATTERN.search(unaccented))
        has_tim_doc = bool(TIM_PREFIX_PATTERN.search(unaccented)) and has_doc_terms
        has_effective_lookup = has_lookup_verbs or has_tim_doc
        if has_comm or has_calendar:
            # Multi-domain only if both an explicit document term AND a lookup verb are present
            # (e.g. "Tìm tài liệu quy định rồi soạn email" or "Tìm quy chế và xếp lịch họp"),
            # otherwise doc terms are email topics and lookup verbs are calendar/mail searches.
            has_research = has_doc_terms and has_effective_lookup
        else:
            has_research = has_doc_terms or has_lookup_verbs

        # Document-topic guard: meeting nouns inside a document read/summarize
        # request ("Tóm tắt văn bản Quy chế tổ chức cuộc họp...") name the TOPIC,
        # not a calendar action. Without this, a pure knowledge request gains a
        # spurious calendar domain and bounces to the supervisor stub on POST /query.
        if (
            has_calendar
            and not has_comm
            and has_doc_terms
            and DOC_READ_PATTERN.search(unaccented)
            and _is_meeting_only_calendar(unaccented_cal)
        ):
            has_calendar = False
            has_research = has_doc_terms or has_effective_lookup

        # 3. Casual conversation or greeting (when no explicit work domain terms are present)
        if CASUAL_PATTERN.search(unaccented) and not (has_comm or has_calendar or has_doc_terms):
            decision = RouteDecision(
                route_type=RouteType.CASUAL_RESPONSE,
                confidence=0.95,
                reasoning="Casual conversation or greeting detected.",
                domains=[Domain.GENERAL],
                complexity=Complexity.DIRECT,
                reason_code="CASUAL_CONVERSATION",
            )
            self._log_route(decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
            return decision

        # 4. Known Static Workflow trigger match (WF-01, WF-02, WF-05)
        # Compiled static workflows take precedence over uncompiled dynamic skills (§5.3 / P19).
        # Schedule inquiries (e.g. "có lịch gì không", "mấy giờ", "rảnh không") preserve CalendarAgent fast-path.
        is_calendar_schedule_inquiry = has_calendar and has_cal_inquiry
        if not is_calendar_schedule_inquiry:
            matched_workflow = self._workflows.match(normalized)
            if matched_workflow is not None:
                is_conflict = matched_workflow.workflow_id == "WF-02" and has_comm
                if not is_conflict:
                    wf_domains = (
                        list(matched_workflow.domains)
                        if matched_workflow.domains
                        else [Domain.GENERAL]
                    )
                    decision = RouteDecision(
                        route_type=RouteType.STATIC_WORKFLOW,
                        target_workflow_id=matched_workflow.workflow_id,
                        confidence=0.95,
                        parameters={"query": normalized},
                        reasoning=f"Matched static workflow trigger for '{matched_workflow.name}'.",
                        domains=wf_domains,
                        complexity=Complexity.MULTI_STEP,
                        reason_code="STATIC_WORKFLOW_MATCH",
                    )
                    self._log_route(decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
                    return decision

            # 5. Dynamic Skill Matching (P14 integration / prototype fallback)
            for skill in self._skills.list_all():
                skill_def = self._skills.get(skill.name, skill.version)
                if skill_def and any(
                    match_trigger(
                        t, normalized, unaccented_query=unaccented, allow_token_subset=False
                    )
                    for t in skill.triggers
                ):
                    caps = [str(c).lower() for c in skill.required_capabilities]
                    has_cap_cal = any("calendar" in c for c in caps)
                    has_cap_mail = any("gmail" in c or "comm" in c for c in caps)
                    has_cap_doc = any("drive" in c or "retrieval" in c for c in caps)

                    active_skill_domains: list[Domain] = []
                    if has_cap_cal:
                        active_skill_domains.append(Domain.CALENDAR)
                    if has_cap_mail:
                        active_skill_domains.append(Domain.COMMUNICATION)
                    if has_cap_doc:
                        active_skill_domains.append(Domain.KNOWLEDGE_RESEARCH)

                    requires_cross_agent = (
                        (has_cap_cal and has_cap_mail)
                        or (has_cap_cal and has_cap_doc)
                        or ("drive.read" in skill.required_capabilities and has_cap_mail)
                    )

                    if requires_cross_agent:
                        decision = RouteDecision(
                            route_type=RouteType.SUPERVISOR_DAG,
                            confidence=0.95,
                            parameters={"skill": skill.name, "query": normalized},
                            reasoning=f"Dynamic multi-domain skill '{skill.name}' requires Supervisor orchestration.",
                            domains=active_skill_domains or [Domain.GENERAL],
                            complexity=Complexity.MULTI_STEP,
                            reason_code=f"SKILL_{skill.name.upper()}",
                        )
                    else:
                        target_agent = (
                            "CommunicationAgent"
                            if has_cap_mail
                            else ("CalendarAgent" if has_cap_cal else "KnowledgeResearchAgent")
                        )
                        domain = (
                            Domain.COMMUNICATION
                            if target_agent == "CommunicationAgent"
                            else (
                                Domain.CALENDAR
                                if target_agent == "CalendarAgent"
                                else Domain.KNOWLEDGE_RESEARCH
                            )
                        )
                        decision = RouteDecision(
                            route_type=RouteType.DIRECT_SPECIALIST,
                            target_agent=target_agent,
                            confidence=0.95,
                            parameters={"skill": skill.name, "query": normalized},
                            reasoning=f"Dynamic skill '{skill.name}' routed directly to {target_agent}.",
                            domains=[domain],
                            complexity=Complexity.MULTI_STEP,
                            reason_code=f"SKILL_{skill.name.upper()}",
                        )
                    self._log_route(decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
                    return decision

        matched_domains: list[tuple[Domain, str]] = []
        if has_calendar:
            matched_domains.append((Domain.CALENDAR, "CalendarAgent"))
        if has_comm:
            matched_domains.append((Domain.COMMUNICATION, "CommunicationAgent"))
        if has_research:
            matched_domains.append((Domain.KNOWLEDGE_RESEARCH, "KnowledgeResearchAgent"))

        # 6. Fast path: exactly 1 domain detected -> DIRECT_SPECIALIST (0 Supervisor LLM tokens)
        if len(matched_domains) == 1:
            domain, agent = matched_domains[0]
            decision = RouteDecision(
                route_type=RouteType.DIRECT_SPECIALIST,
                target_agent=agent,
                confidence=0.95,
                parameters={"query": normalized},
                reasoning=f"Deterministic match for domain '{domain.value}'. Zero Supervisor tokens.",
                domains=[domain],
                complexity=Complexity.DIRECT,
                reason_code=f"SINGLE_DOMAIN_{domain.value.upper()}",
            )
            self._log_route(decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
            return decision

        # 7. Multi-domain query: e.g. "tìm tài liệu rồi gửi mail và tạo lịch họp"
        if len(matched_domains) > 1:
            domains_list = [d for d, _ in matched_domains]
            decision = RouteDecision(
                route_type=RouteType.SUPERVISOR_DAG,
                confidence=0.90,
                parameters={"query": normalized},
                reasoning=f"Multi-domain query spanning {[d.value for d in domains_list]} requires Supervisor DAG planning.",
                domains=domains_list,
                complexity=Complexity.MULTI_STEP,
                reason_code="MULTI_DOMAIN_SUPERVISOR",
            )
            self._log_route(decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
            return decision

        # 8. Casual conversation or greeting
        if CASUAL_PATTERN.search(unaccented):
            decision = RouteDecision(
                route_type=RouteType.CASUAL_RESPONSE,
                confidence=0.95,
                reasoning="Casual conversation or greeting detected.",
                domains=[Domain.GENERAL],
                complexity=Complexity.DIRECT,
                reason_code="CASUAL_CONVERSATION",
            )
            self._log_route(decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
            return decision

        # 9. Stage 2: Built-in Lightweight Heuristic Classifier
        stage2_decision = self._stage2_heuristic_classify(normalized, unaccented)
        if stage2_decision is not None:
            self._log_route(stage2_decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
            return stage2_decision

        # Stage 2: Pluggable External Classifier if provided
        if self._classifier is not None:
            try:
                classified = self._classifier(normalized)
                self._log_route(classified, elapsed_ms=(time.perf_counter() - started) * 1000.0)
                return classified
            except Exception as exc:  # noqa: BLE001
                logger.warning("stage2_classifier_error", error=str(exc))

        # 10. Fallback: Save tokens (§6.1) - Prefer CLARIFICATION over expensive SUPERVISOR
        # Only route to SUPERVISOR_DAG if explicit multi-step conjunctions are present
        has_conjunction = bool(_MULTI_STEP_CONJUNCTIONS.search(unaccented))
        if has_conjunction and len(normalized.split()) >= 4:
            decision = RouteDecision(
                route_type=RouteType.SUPERVISOR_DAG,
                confidence=0.70,
                parameters={"query": normalized},
                reasoning="Multi-step query with connectors routed to Supervisor DAG planner.",
                domains=[Domain.GENERAL],
                complexity=Complexity.OPEN_SUPERVISED,
                reason_code="MULTI_STEP_FALLBACK_SUPERVISOR",
            )
        else:
            decision = RouteDecision(
                route_type=RouteType.CLARIFICATION,
                confidence=0.70,
                reasoning="Ambiguous query without clear domain intent; request clarification to conserve supervisor tokens.",
                domains=[Domain.GENERAL],
                complexity=Complexity.DIRECT,
                reason_code="AMBIGUOUS_QUERY_CLARIFICATION",
            )

        # 11. LLM router fallback: short classification when still ambiguous / low-confidence
        if needs_llm_fallback(decision):
            llm_decision = classify_with_llm(normalized)
            if llm_decision is not None:
                decision = llm_decision

        self._log_route(decision, elapsed_ms=(time.perf_counter() - started) * 1000.0)
        return decision

    def _stage2_heuristic_classify(self, raw_query: str, unaccented: str) -> RouteDecision | None:
        """Stage 2 lightweight classifier resolving conversational action verbs <= 500ms."""
        if STAGE2_BOOKING.search(unaccented):
            return RouteDecision(
                route_type=RouteType.DIRECT_SPECIALIST,
                target_agent="CalendarAgent",
                confidence=0.88,
                parameters={"query": raw_query},
                reasoning="Stage 2 heuristic matched scheduling/appointment action verb.",
                domains=[Domain.CALENDAR],
                complexity=Complexity.DIRECT,
                reason_code="STAGE2_CALENDAR_VERB",
            )

        if STAGE2_MESSAGING.search(unaccented):
            return RouteDecision(
                route_type=RouteType.DIRECT_SPECIALIST,
                target_agent="CommunicationAgent",
                confidence=0.88,
                parameters={"query": raw_query},
                reasoning="Stage 2 heuristic matched messaging/dispatch action verb.",
                domains=[Domain.COMMUNICATION],
                complexity=Complexity.DIRECT,
                reason_code="STAGE2_COMMUNICATION_VERB",
            )

        if STAGE2_LOOKUP.search(unaccented):
            return RouteDecision(
                route_type=RouteType.DIRECT_SPECIALIST,
                target_agent="KnowledgeResearchAgent",
                confidence=0.85,
                parameters={"query": raw_query},
                reasoning="Stage 2 heuristic matched reference/lookup action verb.",
                domains=[Domain.KNOWLEDGE_RESEARCH],
                complexity=Complexity.DIRECT,
                reason_code="STAGE2_RESEARCH_VERB",
            )

        return None

    def _log_route(self, decision: RouteDecision, *, elapsed_ms: float) -> None:
        """Structured logging with structlog and latency budget monitoring (M1 / L5)."""
        logger.info(
            "fast_triage_decision",
            route_type=decision.route_type.value,
            target_agent=decision.target_agent,
            target_workflow_id=decision.target_workflow_id,
            domains=[d.value for d in decision.domains],
            confidence=decision.confidence,
            elapsed_ms=round(elapsed_ms, 3),
            reason_code=decision.reason_code,
        )
        if elapsed_ms > 10.0:
            logger.warning(
                "fast_triage_latency_budget_exceeded",
                elapsed_ms=round(elapsed_ms, 3),
                budget_ms=10.0,
            )
