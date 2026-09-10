"""ContextBuilder assembling relevant memories and entities into a ContextSlice (spec P17-06)."""

from __future__ import annotations

from typing import Any

from app.core.sanitization import sanitize_string
from app.domain.enums import MemoryType
from app.domain.models.context import ContextSlice
from app.domain.models.entity import EntityRecord, EntityResolutionResult
from app.domain.models.evidence import EvidenceItem
from app.domain.models.memory import MemoryItem
from app.domain.models.state import AssistantState
from app.services.context.entity_resolver import EntityResolver
from app.services.context.episodic_service import EpisodicMemoryService
from app.services.context.memory_gate import MemoryGate
from app.services.context.preference_service import PreferenceService
from app.services.retrieval.injection_boundary import wrap_untrusted_memory


class ContextBuilder:
    """Orchestrates memory gating, entity resolution, and memory retrieval into a ContextSlice."""

    def __init__(
        self,
        gate: MemoryGate,
        entity_resolver: EntityResolver,
        preference_service: PreferenceService,
        episodic_service: EpisodicMemoryService,
        max_slice_chars: int = 4000,
    ) -> None:
        self._gate = gate
        self._entity_resolver = entity_resolver
        self._preference_service = preference_service
        self._episodic_service = episodic_service
        self._max_slice_chars = max_slice_chars

    async def build_context(
        self,
        query: str,
        user_id: str,
        state: AssistantState | None = None,
        conversation_context: list[EvidenceItem | dict[str, Any]] | None = None,
    ) -> ContextSlice:
        """Evaluate gate and assemble a tailored ContextSlice without unneeded memory overhead."""
        decision = self._gate.evaluate(query)
        if not decision.should_retrieve:
            return ContextSlice()

        # Compile evidence context from state if not directly supplied
        ctx = list(conversation_context or [])
        if state is not None and state.evidence:
            ctx.extend(state.evidence)

        # 1. Resolve entities if triggered
        resolved_entities: list[EntityRecord] = []
        ambiguous_entities: list[EntityResolutionResult] = []
        if (
            MemoryType.ENTITY in decision.target_memory_types
            or MemoryType.CONVERSATION in decision.target_memory_types
        ):
            resolutions = await self._entity_resolver.resolve_entities(
                query=query, user_id=user_id, conversation_context=ctx
            )
            for r in resolutions:
                if r.is_ambiguous:
                    ambiguous_entities.append(r)
                else:
                    resolved_entities.extend(r.resolved_entities)

        # 2. Retrieve preferences if triggered
        preferences: list[MemoryItem] = []
        if MemoryType.PREFERENCE in decision.target_memory_types:
            preferences = await self._preference_service.get_active_preferences(user_id)

        # 3. Retrieve episodic facts if triggered
        episodic_facts: list[MemoryItem] = []
        if MemoryType.EPISODIC in decision.target_memory_types:
            episodic_facts = await self._episodic_service.retrieve_relevant_episodes(
                user_id=user_id, query=query, limit=3
            )

        # 4. Working memory from state
        working_memory: dict[str, Any] = {}
        if state is not None:
            if state.task_context:
                working_memory.update(state.task_context)
            if state.continuation_context:
                working_memory.update(state.continuation_context)
            working_memory.pop("approval_token", None)
            working_memory.pop("approval_id", None)

        # 5. Render prompt section with sanitization and length bounds
        rendered_prompt = self._render_prompt_section(
            resolved_entities=resolved_entities,
            ambiguous_entities=ambiguous_entities,
            preferences=preferences,
            episodic_facts=episodic_facts,
            working_memory=working_memory,
        )

        return ContextSlice(
            resolved_entities=resolved_entities,
            ambiguous_entities=ambiguous_entities,
            preferences=preferences,
            episodic_facts=episodic_facts,
            working_memory=working_memory,
            rendered_prompt_section=rendered_prompt,
        )

    def _render_prompt_section(
        self,
        resolved_entities: list[EntityRecord],
        ambiguous_entities: list[EntityResolutionResult],
        preferences: list[MemoryItem],
        episodic_facts: list[MemoryItem],
        working_memory: dict[str, Any],
    ) -> str:
        lines: list[str] = []
        if preferences:
            lines.append("### User Preferences:")
            for p in preferences:
                sanitized_pref = sanitize_string(str(p.content), max_string_len=300)
                lines.append(f"- {sanitized_pref}")

        if resolved_entities:
            lines.append("### Resolved Entities:")
            for e in resolved_entities:
                sanitized_name = sanitize_string(str(e.canonical_name), max_string_len=120)
                lines.append(f"- [{e.entity_type.value}] {sanitized_name}")

        if ambiguous_entities:
            lines.append("### Ambiguous Entities (Clarification Required):")
            for a in ambiguous_entities:
                cand_names = [
                    sanitize_string(c.canonical_name, max_string_len=80)
                    for c in a.candidate_entities
                ]
                candidates_str = ", ".join(cand_names)
                ref_str = sanitize_string(a.query_reference, max_string_len=50)
                lines.append(
                    f"- Reference '{ref_str}' has multiple candidates [{candidates_str}]. Please ask user to clarify."
                )

        if episodic_facts:
            lines.append("### Relevant Historical Facts / Events:")
            for f in episodic_facts:
                sanitized_fact = sanitize_string(str(f.content), max_string_len=500)
                lines.append(f"- {wrap_untrusted_memory(sanitized_fact)}")

        if working_memory:
            lines.append("### Active Working Context:")
            for k, v in working_memory.items():
                sanitized_k = sanitize_string(str(k), max_string_len=80)
                sanitized_v = sanitize_string(str(v), max_string_len=500)
                lines.append(f"- {sanitized_k}: {wrap_untrusted_memory(sanitized_v)}")

        rendered = "\n".join(lines)
        if len(rendered) > self._max_slice_chars:
            rendered = (
                rendered[: self._max_slice_chars] + "\n... [Context truncated for token safety]"
            )
        return rendered
