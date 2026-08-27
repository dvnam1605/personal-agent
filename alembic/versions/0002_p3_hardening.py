"""Harden P3 persistence contracts without rewriting the initial migration.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add durable audit delivery, checkpoint concurrency, and forward-safe P9/P18 columns."""
    bind = op.get_bind()

    with op.batch_alter_table("assistant_runs") as batch_op:
        batch_op.add_column(
            sa.Column("state_version", sa.Integer(), nullable=False, server_default="1")
        )

    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("logical_document_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("version_number", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("source_content_hash", sa.String(length=128), nullable=True))
        batch_op.add_column(
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending")
        )
        batch_op.add_column(
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
    op.execute("UPDATE documents SET logical_document_id = id WHERE logical_document_id IS NULL")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.alter_column(
            "logical_document_id", existing_type=sa.String(length=36), nullable=False
        )
        batch_op.create_unique_constraint(
            "uq_documents_version", ["logical_document_id", "version_number"]
        )
    op.create_index(
        "ix_documents_logical_active", "documents", ["logical_document_id", "is_active"]
    )

    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.add_column(sa.Column("parent_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("heading_path", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch_op.add_column(sa.Column("page_start", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("page_end", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("token_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("content_hash", sa.String(length=128), nullable=True))
        batch_op.add_column(
            sa.Column("source_block_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch_op.add_column(
            sa.Column("parent_chunker_version", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("child_chunker_version", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_document_chunks_parent_id", "document_chunks", ["parent_id"], ["id"]
        )
        batch_op.alter_column(
            "heading_path",
            existing_type=sa.JSON(),
            existing_nullable=False,
            server_default=None,
        )
        batch_op.alter_column(
            "source_block_ids",
            existing_type=sa.JSON(),
            existing_nullable=False,
            server_default=None,
        )
    op.create_index("ix_document_chunks_parent_id", "document_chunks", ["parent_id"])

    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.add_column(sa.Column("target", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "important_arguments", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
            )
        )
        batch_op.add_column(
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending")
        )
        batch_op.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("proposal_hash", sa.String(length=128), nullable=True))

    op.create_table(
        "audit_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("event_kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')", name="ck_audit_outbox_status"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["assistant_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_outbox_pending", "audit_outbox", ["status", "created_at"])
    op.create_index("ix_audit_outbox_run", "audit_outbox", ["run_id"])

    if bind.dialect.name == "postgresql":
        op.create_check_constraint(
            "ck_assistant_runs_status",
            "assistant_runs",
            "status IN ('pending', 'running', 'waiting_input', 'waiting_approval', "
            "'completed', 'failed', 'cancelled')",
        )
        op.create_check_constraint(
            "ck_assistant_runs_completed_at",
            "assistant_runs",
            "(status IN ('completed', 'failed', 'cancelled') AND completed_at IS NOT NULL) "
            "OR (status NOT IN ('completed', 'failed', 'cancelled') AND completed_at IS NULL)",
        )


def downgrade() -> None:
    """Remove P3 hardening additions in reverse dependency order."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("ck_assistant_runs_completed_at", "assistant_runs", type_="check")
        op.drop_constraint("ck_assistant_runs_status", "assistant_runs", type_="check")

    op.drop_index("ix_audit_outbox_run", table_name="audit_outbox")
    op.drop_index("ix_audit_outbox_pending", table_name="audit_outbox")
    op.drop_table("audit_outbox")

    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.drop_column("proposal_hash")
        batch_op.drop_column("expires_at")
        batch_op.drop_column("status")
        batch_op.drop_column("important_arguments")
        batch_op.drop_column("target")

    op.drop_index("ix_document_chunks_parent_id", table_name="document_chunks")
    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.drop_constraint("fk_document_chunks_parent_id", type_="foreignkey")
        batch_op.drop_column("child_chunker_version")
        batch_op.drop_column("parent_chunker_version")
        batch_op.drop_column("source_block_ids")
        batch_op.drop_column("content_hash")
        batch_op.drop_column("token_count")
        batch_op.drop_column("page_end")
        batch_op.drop_column("page_start")
        batch_op.drop_column("heading_path")
        batch_op.drop_column("parent_id")

    op.drop_index("ix_documents_logical_active", table_name="documents")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("uq_documents_version", type_="unique")
        batch_op.drop_column("is_active")
        batch_op.drop_column("status")
        batch_op.drop_column("source_content_hash")
        batch_op.drop_column("version_number")
        batch_op.drop_column("logical_document_id")

    with op.batch_alter_table("assistant_runs") as batch_op:
        batch_op.drop_column("state_version")
