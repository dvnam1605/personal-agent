"""Documents generated on request (outline → approval → written sections).

Revision identifiers:
    revision = "0013"
    down_revision = "0012"
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_pg else sa.JSON()
    op.create_table(
        "generated_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("template_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("outline", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source_document_ids", json_type, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("sections", json_type, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("citations", json_type, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("markdown", sa.Text(), nullable=True),
        sa.Column("docx_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="outline"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('outline', 'writing', 'done', 'failed', 'cancelled')",
            name="ck_generated_documents_status",
        ),
    )
    op.create_index(
        "ix_generated_documents_user_status", "generated_documents", ["user_id", "status"]
    )
    op.create_index(
        "ix_generated_documents_conversation", "generated_documents", ["conversation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_generated_documents_conversation", table_name="generated_documents")
    op.drop_index("ix_generated_documents_user_status", table_name="generated_documents")
    op.drop_table("generated_documents")
