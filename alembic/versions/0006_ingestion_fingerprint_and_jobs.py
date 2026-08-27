"""Add ingestion fingerprint tracking and the durable ingestion_jobs table.

Revision ID: 0006
Revises: 0005

P9D wires idempotency end-to-end: documents gain a composite ``fingerprint``
column (hash of source identity + parser/chunker/embedding versions) so
NEW/MODIFIED/UNCHANGED decisions are queryable. ``documents.user_id`` becomes
nullable because pipeline-level ingestion has no end-user attribution.
``ingestion_jobs`` implements the P3 claim pattern for background processing.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = (
    "'QUEUED', 'RUNNING', 'PARSING', 'BUILDING_PARENTS', 'BUILDING_CHILDREN', "
    "'EMBEDDING', 'PERSISTING', 'COMPLETED', 'FAILED', 'SKIPPED', 'NEEDS_OCR'"
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.add_column("documents", sa.Column("fingerprint", sa.String(64), nullable=True))
    op.create_index(
        "ix_documents_logical_fingerprint",
        "documents",
        ["logical_document_id", "fingerprint"],
    )
    if _is_postgres():
        op.alter_column("documents", "user_id", nullable=True)

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(512), nullable=False),
        sa.Column("logical_document_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="QUEUED"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("timings", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("warnings", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("parent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("child_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("table_child_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_by", sa.String(64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()" if _is_postgres() else "CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()" if _is_postgres() else "CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(f"status IN ({_STATUSES})", name="ck_ingestion_jobs_status"),
    )
    op.create_index("ix_ingestion_jobs_status_created", "ingestion_jobs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_status_created", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_index("ix_documents_logical_fingerprint", table_name="documents")
    op.drop_column("documents", "fingerprint")
    # Restore the pre-0006 contract; the deployment is pre-production, so no
    # NULL user rows can exist at downgrade time.
    if _is_postgres():
        op.alter_column("documents", "user_id", nullable=False)
