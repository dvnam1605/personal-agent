"""Generic per-version document profiles (category + typed metadata).

Adds ``document_profiles`` — one row per ``documents`` version holding the
category classification and typed metadata for every document kind
(administrative, report, contract, minutes, technical, academic,
spreadsheet, other). Profiles are refreshed without re-embedding, so the
extractor version lives here instead of in the ingestion fingerprint.

On PostgreSQL the free-form ``metadata`` columns of ``documents`` and
``document_chunks`` also move from JSON to JSONB, and the profile JSON
columns get GIN indexes for entity/tag filters.

Revision identifiers:
    revision = "0011"
    down_revision = "0010"
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

DOC_CATEGORIES = (
    "administrative",
    "report",
    "contract",
    "minutes",
    "technical",
    "academic",
    "spreadsheet",
    "other",
)


def _json_type(is_pg: bool) -> sa.types.TypeEngine:
    return postgresql.JSONB() if is_pg else sa.JSON()


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    json_type = _json_type(is_pg)
    categories = ", ".join(f"'{c}'" for c in DOC_CATEGORIES)

    op.create_table(
        "document_profiles",
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("logical_document_id", sa.String(length=36), nullable=False),
        sa.Column("doc_category", sa.String(length=32), nullable=False, server_default="other"),
        sa.Column("doc_subtype", sa.String(length=128), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("doc_number", sa.String(length=128), nullable=True),
        sa.Column("doc_number_norm", sa.String(length=128), nullable=True),
        sa.Column("issuer", sa.Text(), nullable=True),
        sa.Column("issuer_norm", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("doc_date", sa.Date(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("entities", json_type, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("entity_names_norm", sa.Text(), nullable=True),
        sa.Column("tags", json_type, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("extras", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("field_sources", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("overrides", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column("category_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("extraction_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(f"doc_category IN ({categories})", name="ck_document_profiles_category"),
    )
    op.create_index(
        "ix_document_profiles_category_date", "document_profiles", ["doc_category", "doc_date"]
    )
    op.create_index("ix_document_profiles_number", "document_profiles", ["doc_number_norm"])
    op.create_index("ix_document_profiles_issuer", "document_profiles", ["issuer_norm"])
    op.create_index("ix_document_profiles_logical", "document_profiles", ["logical_document_id"])
    op.create_index("ix_document_profiles_input_hash", "document_profiles", ["input_hash"])

    if not is_pg:
        return  # SQLite unit-migration runs: no JSONB/GIN/partial indexes.

    op.execute(
        "CREATE INDEX ix_document_profiles_entities ON document_profiles "
        "USING gin (entities jsonb_path_ops)"
    )
    op.execute(
        "CREATE INDEX ix_document_profiles_tags ON document_profiles USING gin (tags jsonb_path_ops)"
    )
    op.execute(
        "CREATE INDEX ix_document_profiles_needs_review ON document_profiles (document_id) "
        "WHERE needs_review"
    )
    op.execute("ALTER TABLE documents ALTER COLUMN metadata TYPE jsonb USING metadata::jsonb")
    op.execute("ALTER TABLE document_chunks ALTER COLUMN metadata TYPE jsonb USING metadata::jsonb")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE document_chunks ALTER COLUMN metadata TYPE json USING metadata::json"
        )
        op.execute("ALTER TABLE documents ALTER COLUMN metadata TYPE json USING metadata::json")
        op.execute("DROP INDEX IF EXISTS ix_document_profiles_needs_review")
        op.execute("DROP INDEX IF EXISTS ix_document_profiles_tags")
        op.execute("DROP INDEX IF EXISTS ix_document_profiles_entities")
    op.drop_index("ix_document_profiles_input_hash", table_name="document_profiles")
    op.drop_index("ix_document_profiles_logical", table_name="document_profiles")
    op.drop_index("ix_document_profiles_issuer", table_name="document_profiles")
    op.drop_index("ix_document_profiles_number", table_name="document_profiles")
    op.drop_index("ix_document_profiles_category_date", table_name="document_profiles")
    op.drop_table("document_profiles")
