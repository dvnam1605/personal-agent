"""Rebuild stored text-layer equations as LaTeX — no re-parsing or re-embedding.

Updates the text of parent and child chunks of active documents; embeddings
keep the text-layer form until the document is re-ingested.

Run:
    uv run python scripts/backfill/backfill_formulas.py --dry-run   # preview
    uv run python scripts/backfill/backfill_formulas.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select, update  # noqa: E402

from app.infrastructure.db.models import Document, DocumentChunk  # noqa: E402
from app.infrastructure.db.session import get_engine, get_session_factory  # noqa: E402
from app.services.ingestion.enrichment.formulas import rebuild_in_text  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print, do not write")
    args = parser.parse_args()

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    DocumentChunk.id,
                    DocumentChunk.document_id,
                    DocumentChunk.content_raw,
                    DocumentChunk.content_embedding_text,
                )
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(Document.is_active, DocumentChunk.content_raw.contains("Công thức:"))
                .order_by(DocumentChunk.document_id, DocumentChunk.hierarchy_level)
            )
        ).all()
        print(f"{len(rows)} chunks with text-layer equations")

        caches: dict[str, dict[tuple[str, str], str | None]] = {}
        changed = 0
        for chunk_id, document_id, raw, embedding_text in rows:
            cache = caches.setdefault(document_id, {})
            new_raw = await rebuild_in_text(raw, cache)
            new_embedding_text = await rebuild_in_text(embedding_text, cache)
            if new_raw == raw and new_embedding_text == embedding_text:
                continue
            changed += 1
            if not args.dry_run:
                await session.execute(
                    update(DocumentChunk)
                    .where(DocumentChunk.id == chunk_id)
                    .values(content_raw=new_raw, content_embedding_text=new_embedding_text)
                )
        for document_id, cache in caches.items():
            for (number, _), latex in sorted(cache.items()):
                print(f"{document_id[:8]} ({number}) {latex or '— kept as text'}")
        if args.dry_run:
            print(f"dry run: {changed} chunks would change")
        else:
            await session.commit()
            print(f"updated {changed} chunks")
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
