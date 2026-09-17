"""Durable, chat-scoped replies to Voice requests."""

from alembic import op
import sqlalchemy as sa

revision = "0002_voice_requests"
down_revision = "0001_instance_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "voice_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("reported_at", sa.DateTime(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_voice_requests_channel_id", "voice_requests", ["channel_id"])
    op.create_index("ix_voice_requests_session_id", "voice_requests", ["session_id"])


def downgrade():
    raise RuntimeError("Downgrade is not supported for Sentinel database migrations.")
