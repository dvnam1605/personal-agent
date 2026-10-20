"""Persistence for ``document_profiles`` (per-version category + metadata)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models import Document, DocumentChunk, DocumentProfile
from app.services.ingestion.profiling.profiler import DocumentProfileDraft


@dataclass(frozen=True)
class ProfileTarget:
    """An active document version that needs (re)profiling."""

    document_id: str
    logical_document_id: str
    filename: str
    extractor_version: str | None
    overrides: dict[str, Any]


class SqlAlchemyProfileRepository:
    def __init__(self, session_maker: async_sessionmaker[AsyncSession]) -> None:
        self._session_maker = session_maker

    async def overrides_for(self, logical_document_id: str) -> dict[str, Any]:
        """Manual overrides from the newest profiled version of this document.

        A re-ingested version inherits human corrections instead of losing them.
        """
        async with self._session_maker() as session:
            stmt = (
                select(DocumentProfile.overrides)
                .join(Document, Document.id == DocumentProfile.document_id)
                .where(DocumentProfile.logical_document_id == logical_document_id)
                .order_by(Document.version_number.desc())
                .limit(1)
            )
            value = (await session.execute(stmt)).scalar_one_or_none()
            return dict(value or {})

    async def save(
        self,
        session: AsyncSession,
        *,
        document_id: str,
        logical_document_id: str,
        draft: DocumentProfileDraft,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        row = draft.to_row()
        await session.merge(
            DocumentProfile(
                document_id=document_id,
                logical_document_id=logical_document_id,
                overrides=dict(overrides or {}),
                **row,
            )
        )

    async def save_standalone(self, **kwargs: Any) -> None:
        async with self._session_maker.begin() as session:
            await self.save(session, **kwargs)

    async def targets(self, *, stale_only: bool, extractor_version: str) -> list[ProfileTarget]:
        """Active versions with no profile (or, with ``stale_only``, an old one)."""
        async with self._session_maker() as session:
            stmt = (
                select(
                    Document.id,
                    Document.logical_document_id,
                    Document.title,
                    DocumentProfile.extractor_version,
                    DocumentProfile.overrides,
                )
                .outerjoin(DocumentProfile, DocumentProfile.document_id == Document.id)
                .where(Document.is_active.is_(True))
                .order_by(Document.title)
            )
            rows = (await session.execute(stmt)).all()
        targets = [
            ProfileTarget(
                r.id, r.logical_document_id, r.title, r.extractor_version, dict(r.overrides or {})
            )
            for r in rows
        ]
        if stale_only:
            targets = [t for t in targets if t.extractor_version != extractor_version]
        return targets

    async def document_text(self, document_id: str) -> tuple[str, tuple[str, ...]]:
        """Reassemble a version's text from its parent chunks (no source bytes needed)."""
        async with self._session_maker() as session:
            stmt = (
                select(DocumentChunk.content_raw, DocumentChunk.heading_path)
                .where(
                    DocumentChunk.document_id == document_id,
                    DocumentChunk.hierarchy_level == 0,
                )
                .order_by(DocumentChunk.chunk_index)
            )
            rows = (await session.execute(stmt)).all()
        # Heading text lives only in heading_path (not in content_raw), and a
        # cover page is often nothing but headings ("ĐỒ ÁN TỐT NGHIỆP", topic,
        # author). Re-emit each new heading line so header rules can see it.
        headings: list[str] = []
        lines: list[str] = []
        previous: list[str] = []
        for r in rows:
            path = [str(h) for h in (r.heading_path or [])]
            shared = 0
            while shared < min(len(path), len(previous)) and path[shared] == previous[shared]:
                shared += 1
            for heading in path[shared:]:
                lines.append(heading)
                if heading not in headings:
                    headings.append(heading)
            previous = path
            lines.append(r.content_raw)
        return "\n".join(lines), tuple(headings)
