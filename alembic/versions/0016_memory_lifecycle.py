"""Long-term memory lifecycle columns.

Adds ``memories.is_stale`` (superseded or forgotten items stay out of the
model's context) and ``memories.details`` (category and provenance), plus an
index for listing a user's memories by type. Existing rows are active with
empty details.

Revision identifiers:
    revision = "0016"
    down_revision = "0015"
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("memories") as batch:
        batch.add_column(
            sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
    op.create_index("ix_memories_user_type", "memories", ["user_id", "memory_type"])


def downgrade() -> None:
    op.drop_index("ix_memories_user_type", table_name="memories")
    with op.batch_alter_table("memories") as batch:
        batch.drop_column("details")
        batch.drop_column("is_stale")
