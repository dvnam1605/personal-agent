"""Goals with dated steps, and watch rules.

Adds ``goals`` (long-running work broken into steps the worker reminds about) and
``watch_rules`` (conditions the assistant watches and reports: a sender replies, a
mail mentions a phrase, a new document about a topic arrives).

Revision identifiers:
    revision = "0017"
    down_revision = "0016"
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_pg else sa.JSON()
    owner = sa.ForeignKey("users.id", ondelete="CASCADE")
    op.create_table(
        "goals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), owner, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("due", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("steps", json_type, nullable=False, server_default=sa.text("'[]'")),
        *_timestamps(),
    )
    op.create_index("ix_goals_user_status", "goals", ["user_id", "status"])
    op.create_table(
        "watch_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_watch_rules_user_active", "watch_rules", ["user_id", "active"])


def downgrade() -> None:
    op.drop_index("ix_watch_rules_user_active", table_name="watch_rules")
    op.drop_table("watch_rules")
    op.drop_index("ix_goals_user_status", table_name="goals")
    op.drop_table("goals")
