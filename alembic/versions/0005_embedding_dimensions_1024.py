"""Resize embedding columns to 1024 dimensions for local Vietnamese models.

Revision ID: 0005
Revises: 0004

The project adopted local Vietnamese embedding/reranker models
(AITeamVN/Vietnamese_Embedding = 1024-dim, ViRanker reranker) per approved
ADR 0012. The previous schema assumed OpenAI text-embedding-3-large (1536).
Existing rows are nulled by this migration: the deployment is pre-production
and embeddings are always rebuilt from source documents during ingestion.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _resize(table: str, *, up: bool) -> None:
    """Resize embedding columns.

    PostgreSQL is the only production target for vector storage, so the DDL
    runs there exclusively. SQLite exists solely to exercise the Alembic chain
    in unit tests and cannot ALTER DEFAULTs in place; the ORM-level defaults
    keep the unit-test schema consistent.
    """
    target_dims = 1024 if up else 1536
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector({target_dims}) USING NULL::vector"
    )
    op.execute(f"ALTER TABLE {table} ALTER COLUMN embedding_dimensions SET DEFAULT {target_dims}")


def upgrade() -> None:
    """Switch memories and document_chunks embeddings to the 1024-dim model."""
    _resize("memories", up=True)
    _resize("document_chunks", up=True)


def downgrade() -> None:
    """Restore the original 1536-dim layout."""
    _resize("memories", up=False)
    _resize("document_chunks", up=False)
