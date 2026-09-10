"""Two-stage Token-Pressure Context Compaction engine (spec P17-08, P17-10)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.domain.enums import CompactionStage
from app.domain.models.compaction import CompactionCheckpoint, CompactionTokenPressure
from app.domain.models.specialist import ChatMessage
from app.services.context.tool_pruner import ToolResultPruner


class ContextCompactor:
    """Orchestrates Stage 1 (ToolResultPruning) and Stage 2 (Turn Summarization) under token pressure."""

    def __init__(
        self,
        pruner: ToolResultPruner | None = None,
        summarizer_fn: Callable[[list[ChatMessage]], str] | None = None,
    ) -> None:
        self._pruner = pruner or ToolResultPruner()
        self._summarizer_fn = summarizer_fn or self._default_heuristic_summarizer

    def check_pressure(
        self,
        current_tokens: int,
        context_limit: int,
        threshold_ratio: float = 0.75,
    ) -> CompactionTokenPressure:
        """Evaluate whether token usage exceeds safety threshold."""
        limit = max(1, context_limit)
        is_pressed = current_tokens >= int(limit * threshold_ratio)
        return CompactionTokenPressure(
            current_tokens=current_tokens,
            context_limit=limit,
            threshold_ratio=threshold_ratio,
            is_under_pressure=is_pressed,
        )

    def compact(
        self,
        conversation_id: str,
        messages: Sequence[ChatMessage],
        current_tokens: int,
        context_limit: int,
        threshold_ratio: float = 0.75,
    ) -> tuple[list[ChatMessage], CompactionCheckpoint | None, CompactionStage]:
        """Execute two-stage compaction if under token pressure.

        Returns:
            tuple of (compacted_messages, checkpoint, stage_applied)
        """
        pressure = self.check_pressure(current_tokens, context_limit, threshold_ratio)
        if not pressure.is_under_pressure:
            return list(messages), None, CompactionStage.NONE

        # Stage 1: Model-Free Tool Result Pruning
        pruned_msgs, tokens_saved = self._pruner.prune_messages(messages)
        remaining_tokens = max(0, current_tokens - tokens_saved)

        # Check if Stage 1 relieved the pressure
        if remaining_tokens < int(context_limit * threshold_ratio) or len(messages) <= 4:
            return pruned_msgs, None, CompactionStage.STAGE_1_TOOL_PRUNING

        # Stage 2: Turn Summarization for older turns
        compacted_msgs, checkpoint = self._summarize_shadowed_turns(
            conversation_id, pruned_msgs, remaining_tokens
        )
        return compacted_msgs, checkpoint, CompactionStage.STAGE_2_SUMMARIZATION

    def _summarize_shadowed_turns(
        self,
        conversation_id: str,
        messages: list[ChatMessage],
        estimated_tokens: int,
    ) -> tuple[list[ChatMessage], CompactionCheckpoint]:
        """Summarize older messages, ensuring slice boundary maintains the tool-pairing invariant."""
        # We preserve the system prompt (index 0 if role == 'system') and the last N turns
        start_idx = 1 if messages and messages[0].role == "system" else 0
        total_len = len(messages)

        # Find a clean boundary in the first half of the conversation
        # A clean boundary is immediately before a 'user' or 'assistant' turn that has no dangling tool calls
        candidate_cut = max(start_idx + 1, total_len // 2)

        # Walk backward to find a message whose predecessor was not an open tool call
        cut_idx = candidate_cut
        while cut_idx > start_idx:
            # Slicing at cut_idx means messages[start_idx:cut_idx] are summarized
            # and messages[cut_idx:] are kept.
            # Tool-pairing invariant: messages[cut_idx] must NOT be a 'tool' message
            if messages[cut_idx].role != "tool":
                break
            cut_idx -= 1

        if cut_idx <= start_idx:
            # Cannot safely cut without violating pairing; return as is
            checkpoint = CompactionCheckpoint(
                conversation_id=conversation_id,
                turn_start=0,
                turn_end=0,
                summary="Unable to slice safely without violating tool pairing.",
                shadowed_messages_count=0,
                tokens_reclaimed=0,
            )
            return messages, checkpoint

        slice_to_summarize = messages[start_idx:cut_idx]
        summary_text = self._summarizer_fn(slice_to_summarize)

        summary_msg = ChatMessage(
            role="system",
            content=f"[Context Summary of earlier conversation turns: {summary_text}]",
        )

        final_messages: list[ChatMessage] = []
        if start_idx == 1:
            final_messages.append(messages[0])  # keep original system preamble
        final_messages.append(summary_msg)
        final_messages.extend(messages[cut_idx:])

        # Token savings estimate
        reclaimed = max(0, sum(len(m.content) for m in slice_to_summarize) - len(summary_text)) // 4

        checkpoint = CompactionCheckpoint(
            conversation_id=conversation_id,
            turn_start=start_idx,
            turn_end=cut_idx - 1,
            summary=summary_text,
            shadowed_messages_count=len(slice_to_summarize),
            tokens_reclaimed=reclaimed,
        )

        return final_messages, checkpoint

    @staticmethod
    def _default_heuristic_summarizer(slice_messages: list[ChatMessage]) -> str:
        """Deterministic fallback summarizer without external LLM call."""
        topics: list[str] = []
        for m in slice_messages:
            if m.role == "user":
                topics.append(f"User requested: {m.content[:80]}...")
            elif m.role == "assistant" and "(tool calls)" not in m.content:
                topics.append(f"Assistant responded: {m.content[:80]}...")
        return " | ".join(topics) if topics else "Prior interactive turns summarized."
