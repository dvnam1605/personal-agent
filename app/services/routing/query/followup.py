"""Follow-up resolution: turn a conversational turn into a standalone query.

A turn that points into the previous answer is rewritten into a self-contained
question and its retrieval is scoped to the documents that answer cited; other
follow-ups are condensed with the previous question before triage.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import RouteType
from app.domain.models.routing.route import RouteDecision

# Records keep the orchestrator's logger name.
logger = logging.getLogger("app.services.routing.query.orchestrator")

# Profile-selected documents read together in one scoped RAG pass.
_RAG_MAX_SCOPED_DOCUMENTS = 8


# Per-request follow-up scope: (documents the previous answer cited, the line pointed at).
_FOLLOWUP_SCOPE: ContextVar[tuple[list[str], str] | None] = ContextVar(
    "_FOLLOWUP_SCOPE", default=None
)


FOLLOWUP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^\s*(còn|tiếp|thế còn|vậy còn|ngoài ra|chi tiết hơn|xem thêm|nữa không|hết chưa)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(còn\s+(?:văn\s+bản|tài\s+liệu|quyết\s+định|báo\s+cáo|hợp\s+đồng|biên\s+bản|file)\s+nào"
        r"|còn\s+gì\s+nữa\s+không|còn\s+nữa\s+không|tiếp\s+tục|tiếp\s+đi)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(ông\s+ấy|bà\s+ấy|anh\s+ấy|chị\s+ấy|người\s+đó"
        r"|(?:văn\s+bản|tài\s+liệu|quyết\s+định|báo\s+cáo|hợp\s+đồng|biên\s+bản|file)\s+(?:đó|này|trên))\b",
        re.IGNORECASE,
    ),
]


def is_followup_query(query: str) -> bool:
    """Detect if a user turn is a conversational follow-up depending on prior context."""
    q = query.strip().lower()
    for pattern in FOLLOWUP_PATTERNS:
        if pattern.search(q):
            return True
    words = q.split()
    if len(words) <= 5 and ("còn" in words or "nữa" in words or "tiếp" in words or "hết" in words):
        return True
    return False


# Function words of a follow-up request: they say *how* to answer, not *what*.
_FOLLOWUP_STOPWORDS = frozenset(
    "trinh bay chi tiet cho toi minh em ban hay giai thich ro hon them noi ve cai nay "
    "do kia la gi the nao sao di nhe voi cua va cac nhung mot phan tich lam xem duoc "
    "khong can muon biet ky tom tat mo ta cu the hoi tiep nua o tren duoi "
    "ra hoat dong y nghia vai tro gom bao nhieu".split()
)
_TOKEN = re.compile(r"\w+", re.UNICODE)


def _folded_tokens(text: str) -> list[str]:
    from app.core.text import unaccent_vietnamese

    return _TOKEN.findall(unaccent_vietnamese(text).lower())


def answer_anchor(query: str, history: list[dict[str, str]] | None) -> str | None:
    """The lines of the previous answer this turn points at, if any.

    Lines are scored by shared non-request word pairs; pairs with a number weigh more.
    """
    if not history:
        return None
    answer = next(
        (m.get("content", "") for m in reversed(history) if m.get("role") == "assistant"), ""
    )
    if not answer:
        return None
    tokens = _folded_tokens(query)
    # A turn naming what the answer never mentioned ("Office 2016", "lệnh") asks
    # something new; one built from the answer's own words points into it.
    answer_words = set(_folded_tokens(answer))
    novel = [t for t in tokens if t not in _FOLLOWUP_STOPWORDS and t not in answer_words]
    if any(t.isdigit() for t in novel) or len(novel) >= 2:
        return None
    pairs = [
        (a, b)
        for a, b in zip(tokens, tokens[1:], strict=False)
        if not (a in _FOLLOWUP_STOPWORDS and b in _FOLLOWUP_STOPWORDS)
    ]
    if not pairs:
        return None
    scored: list[tuple[int, int, str]] = []
    for idx, line in enumerate(answer.splitlines()):
        clean = line.strip()
        if not clean:
            continue
        words = _folded_tokens(clean)
        line_pairs = set(zip(words, words[1:], strict=False))
        score = sum(
            2 if (a.isdigit() or b.isdigit()) else 1 for a, b in pairs if (a, b) in line_pairs
        )
        if score:
            scored.append((score, -idx, clean))
    if not scored:
        return None
    best = max(scored)[0]
    lines = [
        text for score, _, text in sorted(scored, key=lambda t: t[1], reverse=True) if score == best
    ]
    excerpt = " ".join(re.sub(r"[*_#>`]|\[\d+\]", "", ln).strip(" -") for ln in lines[:2])
    return excerpt[:600] or None


_STANDALONE_PROMPT = (
    "Viết lại câu hỏi tiếp nối của người dùng thành MỘT câu hỏi độc lập, đầy đủ chủ thể, "
    "bằng tiếng Việt, để tìm trong kho tài liệu. Dùng câu hỏi trước và đoạn câu trả lời mà "
    "người dùng đang nhắc tới để nêu rõ họ hỏi về điều gì (tên khái niệm, tên phần, tên tài "
    "liệu). Giữ yêu cầu của người dùng (trình bày chi tiết, so sánh…). Chỉ trả về câu hỏi."
)


async def standalone_followup(query: str, history: list[dict[str, str]], anchor: str) -> str:
    """A self-contained question for a turn that points into the previous answer."""
    previous = next(
        (m.get("content", "").strip() for m in reversed(history) if m.get("role") == "user"), ""
    )
    fallback = f"{previous} — {anchor} ({query})" if previous else f"{anchor} ({query})"
    try:
        from app.core.llm import llm_text

        rewritten = await llm_text(
            _STANDALONE_PROMPT,
            f"Câu hỏi trước: {previous}\nĐoạn được nhắc tới: {anchor}\nCâu hỏi tiếp nối: {query}",
            timeout=15.0,
            temperature=0.0,
        )
    except Exception:  # noqa: BLE001 - the deterministic condensation is fine
        rewritten = None
    rewritten = (rewritten or "").strip().strip('"')
    if not rewritten or len(rewritten) > 400 or "\n" in rewritten:
        return fallback
    return rewritten


def condense_query_with_history(
    query: str, history: list[dict[str, str]], *, force: bool = False
) -> str:
    """Condense a follow-up query with the prior conversation subject/entities.

    ``force`` condenses even when no follow-up phrase matched.
    """
    if not history or not (force or is_followup_query(query)):
        return query

    last_user_query = ""
    for msg in reversed(history):
        if msg.get("role") == "user":
            last_user_query = msg.get("content", "").strip()
            break

    if not last_user_query:
        return query

    # Substitute the person from the previous question for the pronoun, keeping the rest.
    person = _PERSON_MENTION.search(last_user_query)
    if person and _PERSON_PRONOUN.search(query):
        return _PERSON_PRONOUN.sub(person.group(0).strip(), query, count=1)
    # "còn … nào nữa không", "chi tiết hơn": same question, asking for more.
    return f"{last_user_query} ({query})"


# Honorific + capitalised name, e.g. "ông Nguyễn Văn An" (no names hard-coded).
_PERSON_MENTION = re.compile(
    r"\b(?:ông|bà|anh|chị|đồng chí|đ/c|thầy|cô)\s+"
    r"[A-ZĐÀ-Ỹ][\wÀ-ỹ]*(?:\s+[A-ZĐÀ-Ỹ][\wÀ-ỹ]*){0,4}"
)
_PERSON_PRONOUN = re.compile(r"\b(?:ông|bà|anh|chị|người)\s+(?:ấy|đó|này)\b", re.IGNORECASE)


class FollowupMixin:
    """Conversation history and follow-up handling for ``QueryOrchestrator``."""

    @staticmethod
    async def _load_conversation_history(
        session: AsyncSession,
        conversation_id: str | None,
        user_id: str,
        limit: int = 6,
    ) -> list[dict[str, str]]:
        if not conversation_id:
            return []
        try:
            from app.infrastructure.db.models import Message

            stmt = (
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
            res = await session.execute(stmt)
            msgs = list(reversed(res.scalars().all()))
            return [{"role": m.role, "content": m.content} for m in msgs]
        except Exception:
            logger.warning("load_conversation_history_failed", exc_info=True)
            return []

    @staticmethod
    async def _set_followup_scope(
        session: AsyncSession, conversation_id: str | None, anchor: str | None
    ) -> None:
        """Scope a follow-up's retrieval to what the previous answer cited."""
        _FOLLOWUP_SCOPE.set(None)
        if not anchor or not conversation_id:
            return
        try:
            from app.infrastructure.db.models import Message, RunEvidence

            message = (
                await session.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id, Message.role == "assistant")
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if message is None:
                return
            data = (message.metadata_ or {}).get("data") or {}
            ids = [
                str(c.get("document_id"))
                for c in data.get("citations") or []
                if isinstance(c, dict) and c.get("document_id")
            ]
            if not ids and message.run_id:
                ids = list(
                    (
                        await session.execute(
                            select(RunEvidence.document_id).where(
                                RunEvidence.run_id == message.run_id, RunEvidence.cited
                            )
                        )
                    ).scalars()
                )
            ids = list(dict.fromkeys(i for i in ids if i))[:_RAG_MAX_SCOPED_DOCUMENTS]
            if ids:
                _FOLLOWUP_SCOPE.set((ids, anchor))
        except Exception:  # noqa: BLE001 - an unscoped follow-up still works
            logger.warning("followup_scope_failed", exc_info=True)

    async def _triage_in_context(
        self, query: str, history: list[dict[str, str]] | None
    ) -> tuple[str, RouteDecision, str | None]:
        """Route a turn with its conversation: resolve follow-ups, never ask twice.

        A turn pointing into the previous answer becomes a standalone question; other
        follow-ups and too-vague turns are condensed with the previous question.
        """
        effective_query = query
        anchor = answer_anchor(query, history) if history else None
        if history and anchor:
            effective_query = await standalone_followup(query, history, anchor)
            logger.info(
                "followup_resolved_from_answer",
                extra={"original": query, "standalone": effective_query},
            )
        elif history and is_followup_query(query):
            effective_query = condense_query_with_history(query, history)
            logger.info(
                "condensed_followup_query",
                extra={"original": query, "condensed": effective_query},
            )
        decision = self._triage.triage(effective_query)
        if decision.route_type is RouteType.CLARIFICATION and history and effective_query == query:
            condensed = condense_query_with_history(query, history, force=True)
            if condensed != query:
                retried = self._triage.triage(condensed)
                if retried.route_type is not RouteType.CLARIFICATION:
                    logger.info(
                        "clarification_resolved_from_history",
                        extra={"original": query, "condensed": condensed},
                    )
                    return condensed, retried, None
        return effective_query, decision, anchor
