"""Owner indexes for conversations and documents.

Every conversation list and owner check filters ``conversations.user_id`` (and
orders by ``updated_at``); the image route and document visibility filter
``documents.user_id``. Neither foreign key had an index, so both scans grew with
the table.

Revision identifiers:
    revision = "0020"
    down_revision = "0019"
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Index the owner columns of conversations and documents."""
    op.create_index("ix_conversations_user_updated", "conversations", ["user_id", "updated_at"])
    op.create_index("ix_documents_user", "documents", ["user_id"])


def downgrade() -> None:
    """Drop the owner indexes."""
    op.drop_index("ix_documents_user", table_name="documents")
    op.drop_index("ix_conversations_user_updated", table_name="conversations")
