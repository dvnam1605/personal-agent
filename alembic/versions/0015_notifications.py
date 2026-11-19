"""Notifications left by the proactive worker.

Adds ``notifications`` — morning briefings and meeting dossiers the assistant
prepares without being asked. ``(user_id, dedup_key)`` is unique so each
occurrence (one briefing per day, one dossier per meeting start) is stored once
even when several worker processes run the same job.

Revision identifiers:
    revision = "0015"
    down_revision = "0014"
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_pg else sa.JSON()
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("data", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("user_id", "dedup_key", name="uq_notifications_user_dedup"),
    )
    op.create_index(
        "ix_notifications_user_created", "notifications", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
