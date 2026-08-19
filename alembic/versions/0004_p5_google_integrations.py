"""Add encrypted Google OAuth integration storage.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create one encrypted Google connection per local user."""
    op.create_table(
        "google_integrations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("google_subject", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_type", sa.String(length=32), nullable=False, server_default="Bearer"),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_google_integrations_user", "google_integrations", ["user_id"], unique=True)


def downgrade() -> None:
    """Remove Google OAuth integration storage."""
    op.drop_index("ix_google_integrations_user", table_name="google_integrations")
    op.drop_table("google_integrations")
