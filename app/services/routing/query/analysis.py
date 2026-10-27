"""Spreadsheet analysis on request ("phân tích file doanh thu: tổng theo khu vực")."""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import RouteType
from app.domain.models.routing.route import RouteDecision
from app.services.analysis.service import analyze
from app.services.skills.matching import unaccent_vietnamese

from .minutes import MinutesMixin
from .models import QueryResult, route_info

# Matched on accent-folded text; folding keeps offsets, so names keep their diacritics.
_ANALYZE = re.compile(
    r"^\s*(?:phan tich|thong ke|tinh toan|tinh)\s+(?:file|bang tinh|bang|sheet|du lieu)\s+"
    r"(?P<name>[^:]+?)\s*:\s*(?P<question>\S.*)$"
)


class AnalysisMixin(MinutesMixin):
    """DataAnalyst: answer a question about one of the user's spreadsheets in Drive.

    The model only produces a constrained query plan; computing is done by the
    interpreter in ``services/analysis/plan.py`` over rows read with the read-only Drive
    scope, so no model-written code ever runs.
    """

    async def _maybe_handle_analysis(
        self,
        session: AsyncSession,
        user_id: str,
        query: str,
        run_id: str,
        decision: RouteDecision,
    ) -> QueryResult | None:
        """Handle "phân tích file <tên>: <câu hỏi>"; ``None`` for other turns."""
        if decision.route_type is RouteType.REJECT:
            return None
        match = _ANALYZE.match(unaccent_vietnamese(query))
        if match is None:
            return None
        name = query[match.start("name") : match.end("name")].strip()
        question = query[match.start("question") :].strip()
        result = await analyze(await self._drive(session, user_id), name, question)
        return QueryResult(
            run_id=run_id,
            status="completed",
            message=result.message,
            route=route_info(decision),
            data={"analysis": {"header": result.header, "rows": result.rows, "plan": result.plan}},
        )
