"""Server-side evidence snapshots per run (what the model saw, what it cited).

Adds ``run_evidence`` — one row per evidence unit sent to answer synthesis,
with the exact prompt text, the document version it came from and whether
the answer cited it — and two columns on ``assistant_runs`` linking a run to
its retrieval trace and final answer status. Citations shown later are
rebuilt from these rows instead of trusting client-supplied metadata, and
remain resolvable after re-ingestion (archived versions are kept).

Revision identifiers:
    revision = "0012"
    down_revision = "0011"
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    is_pg = op.get_bind().dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_pg else sa.JSON()

    op.create_table(
        "run_evidence",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("assistant_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("retrieval_trace_id", sa.String(length=64), nullable=True),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=True),
        sa.Column("primary_chunk_id", sa.String(length=64), nullable=True),
        sa.Column("chunk_ids", json_type, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("document_version_id", sa.String(length=36), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("cited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("citation_number", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_run_evidence_run", "run_evidence", ["run_id", "rank"])
    op.create_index("ix_run_evidence_document", "run_evidence", ["document_version_id"])

    with op.batch_alter_table("assistant_runs") as batch_op:
        batch_op.add_column(sa.Column("retrieval_trace_id", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("answer_status", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assistant_runs") as batch_op:
        batch_op.drop_column("answer_status")
        batch_op.drop_column("retrieval_trace_id")
    op.drop_index("ix_run_evidence_document", table_name="run_evidence")
    op.drop_index("ix_run_evidence_run", table_name="run_evidence")
    op.drop_table("run_evidence")
