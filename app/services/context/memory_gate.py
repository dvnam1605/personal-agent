"""Fast heuristic MemoryGate deciding whether memory retrieval is required (spec P17-05)."""

from __future__ import annotations

import re

from app.domain.enums import MemoryType
from app.domain.models.context.memory import MemoryGateDecision

# Patterns indicating trivial standalone requests that require NO memory lookup (<1ms check)
_TRIVIAL_STANDALONE_PATTERNS = [
    # Basic math and calculations (e.g. "2 + 2", "2 + 2 bằng mấy", "100 / 4", "tính 10 * 5")
    re.compile(
        r"^\s*(tính\s+)?[\d\.\,\s\+\-\*\/\^\(\)]+(\s*(bằng|là|ra)\s+(mấy|bao nhiêu)\s*)?\??\s*$",
        re.IGNORECASE,
    ),
    # Greetings, pleasantries, acknowledgments
    re.compile(
        r"^\s*(chào|xin chào|hello|hi|hey|good morning|good afternoon|good evening|tạm biệt|bye|cảm ơn|thank you|thanks|ok|ừ|dạ|vâng)(\s+(bạn|anh|chị|nhiều|nhé|nha|ạ))*\b[.!]?\s*$",
        re.IGNORECASE,
    ),
    # Generic coding requests without personal context
    re.compile(
        r"^\s*(viết|tạo|hướng dẫn|code|script)\s+(hàm|chương trình|giải thuật|thuật toán|code)\s+[\w\s]+\s*$",
        re.IGNORECASE,
    ),
    # General knowledge definitions without personal context
    re.compile(
        r"^\s*(định nghĩa|giải thích|là gì|thủ đô của|dân số|thời tiết|đổi đơn vị)\s+[\w\s\?]+$",
        re.IGNORECASE,
    ),
]

# Patterns indicating contextual references or personal continuity
_CONTEXTUAL_TRIGGERS = [
    # Possessives and personal references
    (
        re.compile(r"\b(của tôi|cho tôi|tôi thích|sở thích|thói quen|gu của tôi)\b", re.IGNORECASE),
        [MemoryType.PREFERENCE, MemoryType.WORKING],
    ),
    # Past events, milestones, previous interactions
    (
        re.compile(
            r"\b(hôm qua|tuần trước|tháng trước|vừa rồi|lần trước|trước đây|đã nói|đã thống nhất|nhắc lại|kết luận)\b",
            re.IGNORECASE,
        ),
        [MemoryType.EPISODIC, MemoryType.WORKING],
    ),
    # Deictic conversation references — require a referent, not bare "đó"/"anh"
    (
        re.compile(
            r"\b((ba\s+)?tài liệu( đó)?|email đó|cuộc họp đó|dự án đó|cái đó|việc đó|điều đó|vừa nói|ở trên|trên này)\b",
            re.IGNORECASE,
        ),
        [MemoryType.CONVERSATION, MemoryType.ENTITY, MemoryType.WORKING],
    ),
    # Specific entity inquiries (person title + name, or a project/meeting noun)
    (
        re.compile(
            r"\b((anh|chị|bác)\s+[\wÀ-ỹ]+|dự án|project|cuộc họp|đối tác|team)\b",
            re.IGNORECASE,
        ),
        [MemoryType.ENTITY, MemoryType.EPISODIC],
    ),
]


class MemoryGate:
    """Zero-latency heuristic gate deciding whether to query memory and which types to fetch."""

    def evaluate(self, query: str) -> MemoryGateDecision:
        """Classify query to determine if memory retrieval is necessary."""
        cleaned = query.strip()
        if not cleaned:
            return MemoryGateDecision(
                should_retrieve=False,
                target_memory_types=[],
                reason="Empty query",
                confidence=1.0,
            )

        # Fast path 1: check for trivial standalone requests
        for pattern in _TRIVIAL_STANDALONE_PATTERNS:
            if pattern.match(cleaned):
                return MemoryGateDecision(
                    should_retrieve=False,
                    target_memory_types=[],
                    reason="Trivial or standalone generic query",
                    confidence=0.98,
                )

        # Fast path 2: detect specific contextual triggers
        target_types: set[MemoryType] = set()
        for pattern, types in _CONTEXTUAL_TRIGGERS:
            if pattern.search(cleaned):
                target_types.update(types)

        if target_types:
            return MemoryGateDecision(
                should_retrieve=True,
                target_memory_types=list(target_types),
                reason="Contextual or personal reference detected in query",
                confidence=0.95,
            )

        # Standalone requests without personal, temporal, deictic, or entity cues bypass memory completely
        return MemoryGateDecision(
            should_retrieve=False,
            target_memory_types=[],
            reason="Standalone query without personal or contextual triggers",
            confidence=0.92,
        )
