"""(Re)build document profiles for stored documents — no re-embedding.

Run:
    uv run python scripts/backfill/backfill_profiles.py --dry-run --no-llm   # preview
    uv run python scripts/backfill/backfill_profiles.py                      # missing/stale only
    uv run python scripts/backfill/backfill_profiles.py --all                # every active doc
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.infrastructure.db.profile_repository import SqlAlchemyProfileRepository  # noqa: E402
from app.infrastructure.db.session import get_engine, get_session_factory  # noqa: E402
from app.services.ingestion.maintenance.profile_backfill import ProfileBackfillJob  # noqa: E402
from app.services.ingestion.profiling import DocumentProfiler  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="re-profile every active document")
    parser.add_argument("--dry-run", action="store_true", help="print, do not write")
    parser.add_argument("--no-llm", action="store_true", help="rules only")
    args = parser.parse_args()

    store = SqlAlchemyProfileRepository(get_session_factory())
    job = ProfileBackfillJob(store, DocumentProfiler(use_llm=not args.no_llm))
    report = await job.run(stale_only=not args.all, dry_run=args.dry_run)

    for filename, d in report.drafts:
        flag = " [REVIEW]" if d.needs_review else ""
        print(
            f"- {filename[:48]:48} {d.doc_category.value:14} conf={d.category_confidence:.2f} "
            f"no={d.doc_number or '-'} date={d.doc_date or '-'}{flag}"
        )
    print(
        f"\nprofiled={report.profiled} needs_review={report.needs_review} "
        f"empty={report.skipped_empty} by_category={report.by_category}"
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
