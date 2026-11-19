"""Find bounded loops that have become stable enough to compile into a static graph.

``docs/architecture/loop-vs-graph.md`` asks that a procedure start as a ReAct loop and
be hardened into a graph only once traces show it is frequent and stable. This module
reads the recorded runs and their tool calls, groups them by route and agent, and
proposes the groups whose dominant tool sequence covers at least ``stability`` of at
least ``min_runs`` runs. It only reports candidates; compiling a graph stays a
deliberate engineering change.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models import AssistantRun, ToolExecution

MIN_RUNS = 50
STABILITY = 0.9
MIN_PATH = 2


@dataclass(frozen=True)
class HardeningCandidate:
    """A route whose runs almost always call the same tools in the same order."""

    route: str
    runs: int
    path: tuple[str, ...]
    share: float
    avg_latency_ms: float
    avg_llm_calls: float


async def find_candidates(
    session: AsyncSession,
    *,
    min_runs: int = MIN_RUNS,
    stability: float = STABILITY,
    since: datetime | None = None,
) -> list[HardeningCandidate]:
    """Groups with enough runs whose most common tool path reaches ``stability``."""
    run_stmt = select(AssistantRun).where(AssistantRun.status == "completed")
    if since is not None:
        run_stmt = run_stmt.where(AssistantRun.created_at >= since)
    runs = {run.id: run for run in (await session.scalars(run_stmt)).all()}
    if not runs:
        return []
    calls = await session.execute(
        select(ToolExecution.run_id, ToolExecution.tool_name)
        .where(ToolExecution.run_id.in_(list(runs)))
        .order_by(ToolExecution.run_id, ToolExecution.executed_at)
    )
    paths: dict[str, list[str]] = defaultdict(list)
    for run_id, tool_name in calls.all():
        paths[run_id].append(tool_name)

    groups: dict[str, list[AssistantRun]] = defaultdict(list)
    for run in runs.values():
        groups[f"{run.route_type}:{run.active_skill or run.workflow_name or '-'}"].append(run)

    candidates: list[HardeningCandidate] = []
    for route, members in groups.items():
        if len(members) < min_runs:
            continue
        counts = Counter(tuple(paths.get(run.id, ())) for run in members)
        path, hits = counts.most_common(1)[0]
        share = hits / len(members)
        if share < stability or len(path) < MIN_PATH:
            continue
        candidates.append(
            HardeningCandidate(
                route=route,
                runs=len(members),
                path=path,
                share=round(share, 3),
                avg_latency_ms=round(sum(r.total_latency_ms for r in members) / len(members), 1),
                avg_llm_calls=round(sum(r.llm_call_count for r in members) / len(members), 2),
            )
        )
    candidates.sort(key=lambda candidate: (-candidate.runs, candidate.route))
    return candidates
