"""Continuable subagent session manager (spec P16-05C / deepseek-harness reference).

Supports two subagent lifecycles:
1. One-shot Worker: Isolated single activation, disposed immediately upon return.
2. Continuable Subagent Session: Multi-turn session retaining conversation history,
   context data, and scoped tool restrictions for follow-up refinement without
   rebuilding full context from scratch.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.sanitization import strip_sensitive_keys
from app.domain.errors import NotFoundError, ValidationError
from app.domain.models.platform.tool import ToolRestriction
from app.domain.models.supervisor import SubagentSessionState
from app.domain.models.supervisor.specialist import ChatMessage

logger = logging.getLogger(__name__)


class ContinuableSessionManager:
    """Manages residency and turn dispatch for continuable subagent sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, SubagentSessionState] = {}

    def create_session(
        self,
        agent_name: str,
        user_id: str,
        parent_run_id: str,
        *,
        depth: int = 0,
        tool_restriction: ToolRestriction | None = None,
        context_data: dict[str, Any] | None = None,
        initial_messages: list[ChatMessage] | None = None,
    ) -> SubagentSessionState:
        """Initialize and register a new continuable subagent session."""
        if not agent_name.strip():
            raise ValidationError("agent_name cannot be blank.")
        if not user_id.strip():
            raise ValidationError("user_id cannot be blank.")

        cleaned = strip_sensitive_keys(dict(context_data or {}))
        session = SubagentSessionState(
            agent_name=agent_name.strip(),
            user_id=user_id.strip(),
            parent_run_id=parent_run_id.strip(),
            depth=depth,
            tool_restriction=tool_restriction,
            context_data=cleaned if isinstance(cleaned, dict) else {},
            messages=list(initial_messages or []),
        )
        self._sessions[session.session_id] = session
        logger.info(
            "subagent_session_created",
            extra={"session_id": session.session_id, "agent_name": agent_name, "depth": depth},
        )
        return session

    def get_session(self, session_id: str) -> SubagentSessionState | None:
        """Retrieve an active session by ID."""
        return self._sessions.get(session_id)

    def require_session(self, session_id: str) -> SubagentSessionState:
        """Retrieve a session or raise NotFoundError."""
        session = self.get_session(session_id)
        if session is None or not session.is_active:
            raise NotFoundError(
                f"Active subagent session '{session_id}' not found.",
                details={"session_id": session_id},
            )
        return session

    def append_message(self, session_id: str, role: str, content: str) -> None:
        """Append a message turn to the session transcript."""
        session = self.require_session(session_id)
        # MessageRole literal validation via ChatMessage model
        msg = ChatMessage(role=role, content=content)  # type: ignore[arg-type]
        session.messages.append(msg)
        session.turn_count += 1

    def update_context(self, session_id: str, data: dict[str, Any]) -> None:
        """Merge additional context data into the session.

        M7: sensitive credentials are stripped before merging to prevent
        multi-turn token leakage through session history.
        """
        session = self.require_session(session_id)
        cleaned = strip_sensitive_keys(dict(data))
        if isinstance(cleaned, dict):
            session.context_data.update(cleaned)

    def close_session(self, session_id: str) -> None:
        """Mark a session as completed/inactive and remove from residency."""
        if session_id in self._sessions:
            self._sessions[session_id].is_active = False
            del self._sessions[session_id]
            logger.info("subagent_session_closed", extra={"session_id": session_id})

    def list_active_sessions(self, user_id: str | None = None) -> list[SubagentSessionState]:
        """List active sessions, optionally filtered by user."""
        active = [s for s in self._sessions.values() if s.is_active]
        if user_id is not None:
            active = [s for s in active if s.user_id == user_id]
        return active
