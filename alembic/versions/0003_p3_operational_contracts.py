"""Close P3 continuation, outbox, retention, and future-table schema contracts."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Make persisted waiting runs and operational delivery/retry state durable."""
    op.execute("UPDATE document_chunks SET heading_path = '[]' WHERE heading_path IS NULL")
    op.execute("UPDATE document_chunks SET source_block_ids = '[]' WHERE source_block_ids IS NULL")
    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.alter_column(
            "heading_path",
            existing_type=sa.JSON(),
            existing_nullable=True,
            nullable=False,
        )
        batch_op.alter_column(
            "source_block_ids",
            existing_type=sa.JSON(),
            existing_nullable=True,
            nullable=False,
        )

    with op.batch_alter_table("documents") as batch_op:
        batch_op.create_check_constraint(
            "ck_documents_status",
            "status IN ('pending', 'processing', 'ready', 'active', 'failed', 'archived')",
        )
        batch_op.create_check_constraint("ck_documents_version_positive", "version_number > 0")
    op.create_index(
        "uq_documents_one_active_version",
        "documents",
        ["logical_document_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
        sqlite_where=sa.text("is_active = 1"),
    )

    op.execute(
        "UPDATE approval_requests SET status = CASE "
        "WHEN approved = true THEN 'approved' "
        "WHEN approved = false THEN 'rejected' "
        "ELSE 'pending' END WHERE status = 'pending' AND approved IS NOT NULL"
    )
    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.create_check_constraint(
            "ck_approval_requests_status",
            "status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled')",
        )
        batch_op.create_check_constraint(
            "ck_approval_requests_decision_consistency",
            "(status = 'pending' AND approved IS NULL) "
            "OR (status = 'approved' AND approved = true) "
            "OR (status IN ('rejected', 'expired', 'cancelled') AND approved = false)",
        )
    op.create_index(
        "ix_approval_requests_run_status",
        "approval_requests",
        ["run_id", "status"],
    )
    op.create_index(
        "uq_approval_requests_pending_proposal",
        "approval_requests",
        ["run_id", "proposal_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )

    with op.batch_alter_table("audit_outbox") as batch_op:
        batch_op.drop_constraint("ck_audit_outbox_status", type_="check")
        batch_op.add_column(sa.Column("claimed_by", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_check_constraint(
            "ck_audit_outbox_status",
            "status IN ('pending', 'processing', 'delivered', 'failed')",
        )
    op.create_index(
        "ix_audit_outbox_retry",
        "audit_outbox",
        ["status", "next_attempt_at"],
    )

    for table in ("tool_executions", "llm_executions", "audit_events"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("outbox_id", sa.String(length=36), nullable=True))
            batch_op.create_unique_constraint(f"uq_{table}_outbox_id", ["outbox_id"])


def downgrade() -> None:
    """Remove operational P3 additions while retaining the original 0002 hardening."""
    for table in ("tool_executions", "llm_executions", "audit_events"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f"uq_{table}_outbox_id", type_="unique")
            batch_op.drop_column("outbox_id")

    op.drop_index("ix_audit_outbox_retry", table_name="audit_outbox")
    with op.batch_alter_table("audit_outbox") as batch_op:
        batch_op.drop_constraint("ck_audit_outbox_status", type_="check")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("claimed_at")
        batch_op.drop_column("claimed_by")
        batch_op.create_check_constraint(
            "ck_audit_outbox_status",
            "status IN ('pending', 'delivered', 'failed')",
        )

    op.drop_index("uq_approval_requests_pending_proposal", table_name="approval_requests")
    op.drop_index("ix_approval_requests_run_status", table_name="approval_requests")
    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.drop_constraint("ck_approval_requests_decision_consistency", type_="check")
        batch_op.drop_constraint("ck_approval_requests_status", type_="check")

    op.drop_index("uq_documents_one_active_version", table_name="documents")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("ck_documents_version_positive", type_="check")
        batch_op.drop_constraint("ck_documents_status", type_="check")

    with op.batch_alter_table("document_chunks") as batch_op:
        batch_op.alter_column(
            "source_block_ids",
            existing_type=sa.JSON(),
            existing_nullable=False,
            nullable=True,
        )
        batch_op.alter_column(
            "heading_path",
            existing_type=sa.JSON(),
            existing_nullable=False,
            nullable=True,
        )
