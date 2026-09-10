"""Unit tests for Phase 17 Token-Pressure Context Compaction & Tool-Result Pruner (spec P17-08..P17-10)."""

from __future__ import annotations

import pytest

from app.domain.enums import CompactionStage
from app.domain.models.specialist import ChatMessage
from app.services.context.compaction import ContextCompactor
from app.services.context.tool_pruner import ToolResultPruner


@pytest.fixture
def tool_pruner() -> ToolResultPruner:
    return ToolResultPruner(min_chars_to_prune=50, keep_recent_turns=1)


@pytest.fixture
def context_compactor(tool_pruner: ToolResultPruner) -> ContextCompactor:
    return ContextCompactor(pruner=tool_pruner)


class TestToolResultPruner:
    """Requirement: Token pressure triggers Stage 1 tool-result pruning without LLM call."""

    def test_prunes_oversized_historical_tool_results_without_llm(
        self, tool_pruner: ToolResultPruner
    ) -> None:
        large_content = (
            "{\n"
            '  "files": [\n'
            '    {"id": "f1", "title": "Quarterly Strategy 2026.pdf", "size": 1048576},\n'
            '    {"id": "f2", "title": "Budget Breakdown Spreadsheet.xlsx", "size": 2097152},\n'
            '    {"id": "f3", "title": "Customer Research Notes.docx", "size": 524288}\n'
            "  ]\n"
            "}" * 5
        )

        messages = [
            ChatMessage(role="system", content="You are a helpful specialist agent."),
            ChatMessage(role="user", content="Search for documents related to strategy"),
            ChatMessage(role="assistant", content="(tool calls)"),
            ChatMessage(role="tool", content=large_content),
            ChatMessage(role="assistant", content="Found 3 strategy documents."),
            ChatMessage(role="user", content="What were their titles?"),
            ChatMessage(role="assistant", content="(tool calls)"),
            ChatMessage(role="tool", content="Short result: 3 files found"),
        ]

        pruned, tokens_saved = tool_pruner.prune_messages(messages)

        # Older tool message was pruned
        assert len(pruned) == len(messages)
        assert "[Tool result pruned:" in pruned[3].content
        assert tokens_saved > 0
        # Recent tool message (turn within keep_recent_turns) is left intact
        assert pruned[7].content == "Short result: 3 files found"

    def test_tool_pairing_invariant_strictly_enforced(self, tool_pruner: ToolResultPruner) -> None:
        """Requirement: Tool-pairing boundary invariant maintained during compaction."""
        # Valid conversation: assistant -> tool -> assistant -> tool
        valid_messages = [
            ChatMessage(role="user", content="Start"),
            ChatMessage(role="assistant", content="(tool calls)"),
            ChatMessage(role="tool", content="A" * 100),
            ChatMessage(role="assistant", content="Done"),
        ]
        assert tool_pruner.verify_tool_pairing_invariant(valid_messages) is True

        pruned, _ = tool_pruner.prune_messages(valid_messages)
        assert tool_pruner.verify_tool_pairing_invariant(pruned) is True

        # Invalid conversation: orphaned tool message at beginning
        invalid_messages = [
            ChatMessage(role="tool", content="Orphan tool result"),
            ChatMessage(role="user", content="Hello"),
        ]
        assert tool_pruner.verify_tool_pairing_invariant(invalid_messages) is False
        # Pruner refuses to touch invalid message list
        unpruned, tokens = tool_pruner.prune_messages(invalid_messages)
        assert unpruned == invalid_messages
        assert tokens == 0


class TestContextCompactor:
    """Requirements: Two-stage execution and summary preserving essential context."""

    def test_no_compaction_when_under_pressure_threshold(
        self, context_compactor: ContextCompactor
    ) -> None:
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Hello"),
            ChatMessage(role="assistant", content="Hi"),
        ]
        compacted, checkpoint, stage = context_compactor.compact(
            conversation_id="conv_1",
            messages=messages,
            current_tokens=1000,
            context_limit=8000,
            threshold_ratio=0.75,
        )
        assert stage == CompactionStage.NONE
        assert checkpoint is None
        assert compacted == messages

    def test_stage_1_pruning_relieves_pressure(self, context_compactor: ContextCompactor) -> None:
        # Tool result with 4000 characters (~1000 tokens)
        big_tool_payload = "SearchResultItem(id=1, name='data')\n" * 100
        messages = [
            ChatMessage(role="system", content="System"),
            ChatMessage(role="user", content="Run search"),
            ChatMessage(role="assistant", content="(tool calls)"),
            ChatMessage(role="tool", content=big_tool_payload),
            ChatMessage(role="assistant", content="Found data"),
            ChatMessage(role="user", content="Next step"),
        ]

        # Token pressure is 6100 with limit 8000 (threshold 75% = 6000) -> pressed!
        compacted, checkpoint, stage = context_compactor.compact(
            conversation_id="conv_1",
            messages=messages,
            current_tokens=6100,
            context_limit=8000,
            threshold_ratio=0.75,
        )
        assert stage == CompactionStage.STAGE_1_TOOL_PRUNING
        assert checkpoint is None
        # Invariant maintained
        assert ToolResultPruner.verify_tool_pairing_invariant(compacted) is True

    def test_stage_2_summarization_on_persistent_pressure(
        self, context_compactor: ContextCompactor
    ) -> None:
        """Requirement: compaction summary preserves essential context across long conversations."""
        # Long conversation without large tool payloads (cannot be resolved by Stage 1 alone)
        messages = [ChatMessage(role="system", content="System prompt instructions")]
        for i in range(10):
            messages.append(
                ChatMessage(
                    role="user", content=f"Step {i}: I need assistance with project deployment."
                )
            )
            messages.append(
                ChatMessage(role="assistant", content=f"Step {i} completed successfully.")
            )

        # 7000 tokens out of 8000 limit
        compacted, checkpoint, stage = context_compactor.compact(
            conversation_id="conv_long",
            messages=messages,
            current_tokens=7000,
            context_limit=8000,
            threshold_ratio=0.75,
        )
        assert stage == CompactionStage.STAGE_2_SUMMARIZATION
        assert checkpoint is not None
        assert checkpoint.conversation_id == "conv_long"
        assert checkpoint.turn_end > checkpoint.turn_start
        assert checkpoint.shadowed_messages_count > 0

        # Verify compacted messages: system prompt + summary system message + recent turns
        assert compacted[0].role == "system"
        assert "[Context Summary of earlier conversation turns:" in compacted[1].content
        # Tool pairing invariant verified
        assert ToolResultPruner.verify_tool_pairing_invariant(compacted) is True
