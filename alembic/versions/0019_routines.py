"""Routines: requests the user saved to run on a schedule.

Revision identifiers:
    revision = "0019"
    down_revision = "0018"
"""

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routines",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("weekdays", sa.String(length=20), nullable=False),
        sa.Column("run_time", sa.Time(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_routines_user_active", "routines", ["user_id", "active"])


def downgrade() -> None:
    op.drop_index("ix_routines_user_active", table_name="routines")
    op.drop_table("routines")
