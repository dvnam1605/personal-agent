"""Pause a request to ask the user, and resume it with the answer on the next turn."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import Conversation
from app.services.critic.draft import EMAIL_RE
from app.services.skills.matching import unaccent_vietnamese

from .drafting import RESUME_EMAIL_RECIPIENTS
from .models import QueryResult
from .web import WebMixin

PENDING_KEY = "pending_clarification"
PENDING_TTL = timedelta(minutes=30)
_CANCEL = re.compile(r"^\s*(?:huy|thoi|bo qua|khong can|cancel)\b")


@dataclass(frozen=True)
class ResumeOutcome:
    """What to do with this turn: run ``query`` (possibly resumed) or reply ``message``."""

    query: str
    message: str | None = None


class ClarificationMixin(WebMixin):
    """Human-in-the-loop questions in the middle of a request.

    A handler that cannot continue without the user (no confirmed recipient for a draft)
    returns ``clarification_needed`` with ``data["resume"]``; the orchestrator stores it
    on the conversation. The next message in that conversation is read as the answer: it
    is merged into the original request, which runs again from the top, so every check
    (routing, review, approval) applies to the resumed request as to a fresh one. A
    pending question expires after ``PENDING_TTL`` and "hủy" drops it.
    """

    async def _pending_conversation(
        self, session: AsyncSession, conversation_id: str | None, user_id: str
    ) -> Conversation | None:
        if not conversation_id:
            return None
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user_id:
            return None
        return conversation

    async def _resume_pending(
        self, session: AsyncSession, conversation_id: str | None, user_id: str, query: str
    ) -> ResumeOutcome:
        """Merge an answer into the request it answers; pass other turns through."""
        conversation = await self._pending_conversation(session, conversation_id, user_id)
        metadata = dict(conversation.metadata_ or {}) if conversation is not None else {}
        pending = metadata.get(PENDING_KEY)
        if conversation is None or not isinstance(pending, dict):
            return ResumeOutcome(query=query)
        expires = _parse_time(pending.get("expires_at"))
        if expires is None or expires < datetime.now(UTC):
            self._set_pending(conversation, None)
            return ResumeOutcome(query=query)
        if _CANCEL.match(unaccent_vietnamese(query)):
            self._set_pending(conversation, None)
            return ResumeOutcome(query=query, message="Đã hủy yêu cầu đang chờ.")
        if pending.get("kind") == RESUME_EMAIL_RECIPIENTS:
            addresses = EMAIL_RE.findall(query)
            if not addresses:
                return ResumeOutcome(
                    query=query,
                    message=(
                        "Tôi cần địa chỉ email của người nhận, ví dụ nam@congty.vn "
                        "(hoặc nói “hủy”)."
                    ),
                )
            self._set_pending(conversation, None)
            original = str(pending.get("query") or "")
            return ResumeOutcome(query=f"{original} (gửi tới {', '.join(addresses)})")
        self._set_pending(conversation, None)
        return ResumeOutcome(query=query)

    async def _remember_pending(
        self,
        session: AsyncSession,
        conversation_id: str | None,
        user_id: str,
        result: QueryResult,
    ) -> None:
        """Store the question a handler asked so the next turn can resume it."""
        resume = result.data.get("resume") if isinstance(result.data, dict) else None
        if result.status != "clarification_needed" or not isinstance(resume, dict):
            return
        conversation = await self._pending_conversation(session, conversation_id, user_id)
        if conversation is None:
            return
        expires_at = (datetime.now(UTC) + PENDING_TTL).isoformat()
        self._set_pending(conversation, {**resume, "expires_at": expires_at})

    @staticmethod
    def _set_pending(conversation: Conversation, pending: dict[str, object] | None) -> None:
        metadata = dict(conversation.metadata_ or {})
        if pending is None:
            metadata.pop(PENDING_KEY, None)
        else:
            metadata[PENDING_KEY] = pending
        conversation.metadata_ = metadata


def _parse_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
