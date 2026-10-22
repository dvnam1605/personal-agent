"""Typed relations between documents, people and organisations.

Adds ``document_relations`` — one row per relation stated by a document
version: which documents it replaces, amends or is based on, who signed it,
who it awards, appoints or transfers. Relation questions ("văn bản nào căn
cứ Nghị định 152/2025", "ông X đã ký những văn bản nào") are answered from
this table with SQL instead of a graph database.

Revision identifiers:
    revision = "0014"
    down_revision = "0013"
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_pg else sa.JSON()
    op.create_table(
        "document_relations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("logical_document_id", sa.String(length=36), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("target_text", sa.Text(), nullable=False),
        sa.Column("target_norm", sa.Text(), nullable=False),
        sa.Column("target_number_norm", sa.String(length=128), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("attributes", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_document_relations_document", "document_relations", ["document_id"])
    op.create_index(
        "ix_document_relations_type_number",
        "document_relations",
        ["relation_type", "target_number_norm"],
    )
    op.create_index(
        "ix_document_relations_type_target", "document_relations", ["relation_type", "target_norm"]
    )


def downgrade() -> None:
    op.drop_index("ix_document_relations_type_target", table_name="document_relations")
    op.drop_index("ix_document_relations_type_number", table_name="document_relations")
    op.drop_index("ix_document_relations_document", table_name="document_relations")
    op.drop_table("document_relations")
