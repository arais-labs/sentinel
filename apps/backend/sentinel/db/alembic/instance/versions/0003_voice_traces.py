"""Instance-scoped Voice execution traces, independent of chat sessions."""

from alembic import op
import sqlalchemy as sa

revision = "0003_voice_traces"
down_revision = "0002_voice_requests"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "voice_traces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_voice_traces_created_at", "voice_traces", ["created_at"])
    op.create_table(
        "voice_trace_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "trace_id",
            sa.Uuid(),
            sa.ForeignKey("voice_traces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_voice_trace_events_trace_id", "voice_trace_events", ["trace_id"])


def downgrade():
    raise RuntimeError("Downgrade is not supported for Sentinel database migrations.")
