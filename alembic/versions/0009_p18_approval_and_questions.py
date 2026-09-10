"""P18 schema updates: unavailable approval status and user_questions table.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-09
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # token_hash stores SHA-256 of the minted execution token; plaintext tokens
    # are returned once over HTTP and never persisted (M8).
    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.add_column(sa.Column("token_hash", sa.String(length=64), nullable=True))
        batch_op.drop_constraint("ck_approval_requests_status", type_="check")
        batch_op.drop_constraint("ck_approval_requests_decision_consistency", type_="check")
        batch_op.create_check_constraint(
            "ck_approval_requests_status",
            "status IN ('pending', 'approved', 'rejected', 'expired', 'cancelled', 'unavailable')",
        )
        batch_op.create_check_constraint(
            "ck_approval_requests_decision_consistency",
            "(status = 'pending' AND approved IS NULL) "
            "OR (status = 'approved' AND approved = true) "
            "OR (status IN ('rejected', 'expired', 'cancelled', 'unavailable') AND approved = false)",
        )

    # Legacy pending rows without a proposal_hash cannot be tamper-checked; expire them.
    op.execute(
        sa.text(
            "UPDATE approval_requests SET status = 'unavailable', approved = false "
            "WHERE proposal_hash IS NULL AND status = 'pending'"
        )
    )

    # 2. Create user_questions table (preserves audit records on run purge)
    op.create_table(
        "user_questions",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("assistant_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("questions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("answers", sa.JSON(), nullable=True),
        sa.Column("answered_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('pending', 'answered', 'cancelled', 'expired')",
            name="ck_user_questions_status",
        ),
    )
    op.create_index(
        "ix_user_questions_run_status",
        "user_questions",
        ["run_id", "status"],
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Revision 0009 cannot be downgraded without dropping user_questions and "
        "approval token_hash. Restore from backup instead of running alembic downgrade."
    )
