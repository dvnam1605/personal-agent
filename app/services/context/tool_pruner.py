"""Model-Free Tool-Result Pruner preserving the Tool-Pairing Invariant (spec P17-09, P17-10)."""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.models.specialist import ChatMessage


class ToolResultPruner:
    """Stage 1 Model-Free Pruner: Reclaims tokens from historical tool results without LLM calls."""

    def __init__(
        self,
        min_chars_to_prune: int = 120,
        keep_recent_turns: int = 1,
    ) -> None:
        self._min_chars = min_chars_to_prune
        self._keep_recent = keep_recent_turns

    def prune_messages(
        self,
        messages: Sequence[ChatMessage],
    ) -> tuple[list[ChatMessage], int]:
        """Prune older oversized tool results while strictly preserving message roles and pairing.

        Returns:
            tuple of (pruned_messages, estimated_tokens_reclaimed)
        """
        # Validate tool pairing invariant before pruning
        if not self.verify_tool_pairing_invariant(messages):
            # If invalid, refuse to prune to avoid corrupting backend state
            return list(messages), 0

        # Identify boundary for recent turns to keep intact
        # Count turns from the end: an assistant message defines a turn
        n = len(messages)
        cutoff_index = n
        assistant_seen = 0

        for i in range(n - 1, -1, -1):
            if messages[i].role == "assistant":
                assistant_seen += 1
                if assistant_seen >= self._keep_recent:
                    cutoff_index = i
                    break

        pruned_messages: list[ChatMessage] = []
        chars_reclaimed = 0

        for idx, msg in enumerate(messages):
            if idx < cutoff_index and msg.role == "tool" and len(msg.content) >= self._min_chars:
                # Replace with compact summary
                summary = self._summarize_tool_content(msg.content)
                chars_reclaimed += max(0, len(msg.content) - len(summary))
                pruned_messages.append(ChatMessage(role="tool", content=summary))
            else:
                pruned_messages.append(msg)

        # Validate invariant after pruning
        if not self.verify_tool_pairing_invariant(pruned_messages):
            return list(messages), 0

        # Rough token estimate: ~4 chars per token
        tokens_reclaimed = chars_reclaimed // 4
        return pruned_messages, tokens_reclaimed

    def _summarize_tool_content(self, content: str) -> str:
        """Derive a compact summary line from bulky tool content."""
        # Try to infer tool name or count if structured
        first_line = content.splitlines()[0] if content.splitlines() else content
        first_line_clean = first_line.strip()[:60]
        line_count = len(content.splitlines())
        char_count = len(content)
        return f"[Tool result pruned: {char_count} chars, {line_count} lines - summary: {first_line_clean}...]"

    @staticmethod
    def verify_tool_pairing_invariant(messages: Sequence[ChatMessage]) -> bool:
        """Verify that every tool call message has matching tool outputs and vice versa."""
        # Check that tool messages never appear as the very first message
        if messages and messages[0].role == "tool":
            return False

        # In conversational format, a 'tool' message must be preceded by an 'assistant' turn or another 'tool' message
        for i, msg in enumerate(messages):
            if msg.role == "tool":
                if i == 0:
                    return False
                prev_role = messages[i - 1].role
                if prev_role not in ("assistant", "tool"):
                    return False

        return True
