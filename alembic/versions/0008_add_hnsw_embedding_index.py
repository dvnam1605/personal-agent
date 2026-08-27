"""HNSW cosine ANN index over CHILD embedding vectors (spec P10-03, latency).

Dense retrieval orders candidates by ``embedding <=> :query`` on every search;
without a matching index PostgreSQL performs a sequential scan over all stored
vectors per query. pgvector's HNSW ``vector_cosine_ops`` class turns that into
an approximate-nearest-neighbour lookup consistent with the P10 latency budget
traced in P10D.

Scope note: only ``document_chunks`` is indexed here — it is the unit the
P10A-P10D retrieval engine searches. ``memories.embedding`` stays unindexed
until its consumer phase justifies the maintenance cost.

Revision identifiers:
    revision = "0008"
    down_revision = "0007"
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_HNSW_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS ix_document_chunks_embedding_hnsw "
    "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite unit-migration runs exercise chain continuity only.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(_HNSW_INDEX_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
