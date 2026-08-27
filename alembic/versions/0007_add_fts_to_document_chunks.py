"""Full-text search over document_chunks (spec P10A).

Creates the ``vietnamese_simple`` text-search configuration (``simple``
copied with an unaccent mapping — required for accent-insensitive matching of
Vietnamese content), a generated ``search_vector`` tsvector column over
``content_raw``, and its GIN index.

Revision identifiers:
    revision = "0007"
    down_revision = "0006"
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_UNACCENT_TOKEN_TYPES = "asciihword, hword_asciipart, word, hword, hword_part"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # FTS is PostgreSQL-only; sqlite unit-migration runs skip it.

    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(
        "CREATE TEXT SEARCH CONFIGURATION IF NOT EXISTS public.vietnamese_simple "
        "( COPY = simple )"
    )
    op.execute(
        f"ALTER TEXT SEARCH CONFIGURATION public.vietnamese_simple "
        f"ALTER MAPPING FOR {_UNACCENT_TOKEN_TYPES} WITH unaccent, simple"
    )
    op.add_column(
        "document_chunks",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('public.vietnamese_simple', content_raw)",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_document_chunks_search_vector",
        "document_chunks",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.drop_index("ix_document_chunks_search_vector", table_name="document_chunks")
    op.drop_column("document_chunks", "search_vector")
    op.execute("DROP TEXT SEARCH CONFIGURATION IF EXISTS public.vietnamese_simple")
