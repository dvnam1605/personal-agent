"""User-defined routines in the query orchestrator ("tạo quy trình mỗi thứ 6 17h: …")."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import RouteType
from app.domain.models.routing.route import RouteDecision
from app.services.routines.commands import describe, parse_routine_command
from app.services.routines.service import RoutineError, RoutineService

from .analysis import AnalysisMixin
from .models import QueryResult, route_info


class RoutineMixin(AnalysisMixin):
    """Save a request with a weekly schedule, list routines, and switch them off.

    Saving a routine stores text only; when it runs, the proactive worker sends it
    through this orchestrator again, so it gets no permission the user's own question
    would not have.
    """

    async def _maybe_handle_routines(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        decision: RouteDecision,
    ) -> QueryResult | None:
        """Handle a routine command; ``None`` when the turn is not one."""
        if decision.route_type is RouteType.REJECT:
            return None
        command = parse_routine_command(query)
        if command is None:
            return None
        if command.kind == "create" and command.run_time is not None:
            try:
                routine = await RoutineService.create(
                    session, user_id, command.query, command.weekdays, command.run_time
                )
            except RoutineError as exc:
                message = str(exc)
            else:
                message = (
                    f"Đã tạo quy trình «{routine.name}» — {describe(command.weekdays, command.run_time)}. "
                    "Kết quả sẽ có trong chuông thông báo; việc cần ghi vẫn chờ bạn duyệt."
                )
        elif command.kind == "list":
            routines = await RoutineService.active(session, user_id)
            if not routines:
                message = "Bạn chưa có quy trình nào. Ví dụ: «tạo quy trình mỗi thứ 6 17h: tổng hợp email tuần này»."
            else:
                lines = ["Các quy trình đang bật:"]
                lines.extend(
                    f"{index}. {r.name} — {describe(RoutineService.days(r), r.run_time)}"
                    for index, r in enumerate(routines, start=1)
                )
                message = "\n".join(lines)
        else:
            stopped = await RoutineService.delete(session, user_id, command.target)
            message = (
                "Đã tắt: " + ", ".join(f"«{r.name}»" for r in stopped)
                if stopped
                else "Không có quy trình nào khớp."
            )
        return QueryResult(
            run_id=run_id, status="completed", message=message, route=route_info(decision)
        )
