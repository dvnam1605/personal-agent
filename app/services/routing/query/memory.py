"""Long-term memory in the query orchestrator: memory commands and the personal context."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import RouteType
from app.domain.models.routing.route import RouteDecision
from app.services.context.personal_memory import (
    PersonalMemoryService,
    parse_memory_command,
    recall_message,
)
from app.services.context.sql_memory_store import SqlMemoryStore

from .knowledge import KnowledgeMixin
from .models import QueryResult, route_info


class MemoryMixin(KnowledgeMixin):
    """Answers "nhớ rằng …" / "quên …" / "bạn nhớ gì về tôi" and feeds memory to drafts.

    Memory commands are handled before routing, in both the JSON and the streaming path,
    and never reach the model. The personal context is read from the request session so
    it reflects a memory stored earlier in the same conversation.
    """

    def _memory_for(self, session: AsyncSession) -> PersonalMemoryService:
        if self._memory_factory is not None:
            return self._memory_factory(session)
        return PersonalMemoryService(SqlMemoryStore(session))

    async def _maybe_handle_memory(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        decision: RouteDecision,
    ) -> QueryResult | None:
        """Handle a memory command; ``None`` when the turn is a normal request."""
        if decision.route_type is RouteType.REJECT:
            return None
        command = parse_memory_command(query)
        if command is None:
            return None
        memory = self._memory_for(session)
        data: dict[str, Any] = {"memory_command": command.kind}
        if command.kind == "remember":
            item = await memory.remember(user_id, command.content)
            message = f"Đã ghi nhớ: {item.content}"
            data["memory_id"] = item.id
        elif command.kind == "forget":
            result = await memory.forget(user_id, command.content)
            if result.ambiguous:
                message = (
                    f"Có {len(result.ambiguous)} ghi nhớ khớp với «{command.content}». "
                    "Bạn nói cụ thể hơn, hoặc xóa từng mục trong trang Bộ nhớ."
                )
            elif result.forgotten:
                lines = ["Đã quên:", *(f"- {item.content}" for item in result.forgotten)]
                message = "\n".join(lines)
            else:
                message = f"Tôi không có ghi nhớ nào khớp với «{command.content}»."
            data["forgotten"] = [item.id for item in result.forgotten]
        else:
            message = recall_message(await memory.active(user_id))
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=message,
            route=route_info(decision),
            data=data,
        )

    async def _personal_context(self, session: AsyncSession, user_id: str, query: str) -> str:
        """The user's remembered preferences and relevant facts, or an empty string."""
        return await self._memory_for(session).context_for(user_id, query)
