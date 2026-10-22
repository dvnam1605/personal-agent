"""(Re)build document relations for stored documents — no re-parsing or re-embedding.

Text is rebuilt from each version's parent chunks and the signer comes from
its profile, so run ``backfill_profiles.py`` first when profiles are stale.

Run:
    uv run python scripts/backfill/backfill_relations.py --dry-run --no-llm   # preview, rules only
    uv run python scripts/backfill/backfill_relations.py                      # not yet extracted
    uv run python scripts/backfill/backfill_relations.py --all                # every active doc
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.infrastructure.db.profile_repository import SqlAlchemyProfileRepository  # noqa: E402
from app.infrastructure.db.relation_repository import SqlAlchemyRelationRepository  # noqa: E402
from app.infrastructure.db.session import get_engine, get_session_factory  # noqa: E402
from app.services.ingestion.relations import DocumentRelationExtractor  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="re-extract every active document")
    parser.add_argument("--dry-run", action="store_true", help="print, do not write")
    parser.add_argument("--no-llm", action="store_true", help="rules + signer only")
    args = parser.parse_args()

    factory = get_session_factory()
    relations = SqlAlchemyRelationRepository(factory)
    profiles = SqlAlchemyProfileRepository(factory)
    extractor = DocumentRelationExtractor(use_llm=not args.no_llm)
    totals: Counter[str] = Counter()

    targets = await relations.targets(stale_only=not args.all)
    for target in targets:
        text, _ = await profiles.document_text(target.document_id)
        drafts = await extractor.aextract(text, category=target.category, entities=target.entities)
        counts = Counter(d.relation_type.value for d in drafts)
        totals.update(counts)
        print(f"- {target.filename[:48]:48} {dict(sorted(counts.items()))}")
        if not args.dry_run:
            await relations.replace_standalone(
                document_id=target.document_id,
                logical_document_id=target.logical_document_id,
                drafts=drafts,
            )
    print(
        f"\ndocuments={len(targets)} relations={sum(totals.values())} "
        f"by_type={dict(sorted(totals.items()))}"
        + (" (dry run, nothing written)" if args.dry_run else "")
    )
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
