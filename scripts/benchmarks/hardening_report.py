"""Report bounded loops stable enough to compile into a static graph (loop → graph).

Run against the live database:
    uv run python scripts/benchmarks/hardening_report.py
    uv run python scripts/benchmarks/hardening_report.py --min-runs 20 --stability 0.85 --days 30

Prints each candidate route with its dominant tool sequence, how many runs follow it,
and the average latency and LLM calls a static graph would aim to cut.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.infrastructure.db.session import get_session_factory  # noqa: E402
from app.services.skills.hardening import MIN_RUNS, STABILITY, find_candidates  # noqa: E402


async def main(min_runs: int, stability: float, days: int | None) -> int:
    since = datetime.now(UTC) - timedelta(days=days) if days else None
    async with get_session_factory()() as session:
        candidates = await find_candidates(
            session, min_runs=min_runs, stability=stability, since=since
        )
    if not candidates:
        print(f"Chưa có route nào đủ {min_runs} lượt và ổn định ≥ {stability:.0%}.")
        return 0
    for candidate in candidates:
        print(f"{candidate.route}: {candidate.runs} lượt, {candidate.share:.0%} theo cùng chuỗi")
        print(f"    {' → '.join(candidate.path)}")
        print(
            f"    trung bình {candidate.avg_latency_ms:.0f} ms, {candidate.avg_llm_calls} lần gọi LLM"
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-runs", type=int, default=MIN_RUNS)
    parser.add_argument("--stability", type=float, default=STABILITY)
    parser.add_argument("--days", type=int, default=None)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.min_runs, args.stability, args.days)))
