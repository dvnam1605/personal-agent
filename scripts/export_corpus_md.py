"""Export the active corpus as Markdown files, one per document (read-only).

Lets another retrieval system run on exactly the text this system indexed.
Each file has YAML front matter with the document profile, then the
document's top-level chunks in reading order under their section headings.

Run inside the backend container (it has the DB settings):

    docker cp scripts/export_corpus_md.py assistant_backend:/tmp/
    docker exec -e PYTHONPATH=/app -w /app assistant_backend \\
        python /tmp/export_corpus_md.py --out /tmp/corpus_md
    docker cp assistant_backend:/tmp/corpus_md ./corpus_md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

from sqlalchemy import select

from app.infrastructure.db.models import Document, DocumentChunk, DocumentProfile
from app.infrastructure.db.session import get_engine, get_session_factory


def _slug(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("_")[:80] or "doc"


def _yaml(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)  # JSON scalars are valid YAML


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="output directory")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    factory = get_session_factory()
    async with factory() as session:
        docs = (
            await session.execute(
                select(Document, DocumentProfile)
                .outerjoin(DocumentProfile, DocumentProfile.document_id == Document.id)
                .where(Document.is_active)
            )
        ).all()
        chunks = (
            await session.execute(
                select(
                    DocumentChunk.document_id,
                    DocumentChunk.content_raw,
                    DocumentChunk.heading_path,
                    DocumentChunk.page_start,
                )
                .where(DocumentChunk.hierarchy_level == 0)
                .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
            )
        ).all()

    by_doc: dict[str, list[tuple[str, list[str], int | None]]] = {}
    for doc_id, text, heading_path, page in chunks:
        by_doc.setdefault(doc_id, []).append((text, list(heading_path or []), page))

    manifest = []
    used: set[str] = set()
    for doc, profile in docs:
        signer = ""
        if profile is not None:
            signer = next(
                (
                    str(e.get("name") or "")
                    for e in profile.entities or []
                    if isinstance(e, dict) and e.get("kind") == "signer"
                ),
                "",
            )
        meta = {
            "document_id": doc.id,
            "logical_document_id": doc.logical_document_id,
            "source_title": doc.title,
            "category": profile.doc_category if profile else None,
            "doc_type": profile.doc_subtype if profile else None,
            "doc_number": profile.doc_number if profile else None,
            "doc_date": profile.doc_date.isoformat() if profile and profile.doc_date else None,
            "title": profile.title if profile else None,
            "issuer": profile.issuer if profile else None,
            "signer": signer or None,
        }
        name = _slug(meta["doc_number"] or doc.title or doc.id)
        while name in used:
            name += "_"
        used.add(name)

        lines = ["---", *(f"{k}: {_yaml(v)}" for k, v in meta.items()), "---", ""]
        if meta["title"]:
            lines += [f"# {meta['title']}", ""]
        last_heading = None
        for text, heading_path, page in by_doc.get(doc.id, []):
            heading = heading_path[-1] if heading_path else None
            if heading and heading != last_heading:
                lines += [f"## {heading}", ""]
                last_heading = heading
            if page:
                lines.append(f"<!-- page {page} -->")
            lines += [text.strip(), ""]
        path = args.out / f"{name}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        manifest.append({**meta, "file": path.name, "chunks": len(by_doc.get(doc.id, []))})

    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"exported {len(manifest)} documents to {args.out}")
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
