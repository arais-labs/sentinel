"""Voice is a hidden session kind; Voice requests are replaced by direct agent messages."""

from alembic import op
import sqlalchemy as sa

revision = "0004_voice_session"
down_revision = "0003_voice_traces"
branch_labels = None
depends_on = None


def upgrade():
    # A plain ADD COLUMN: never rebuild "sessions". A batch rebuild drops the old table
    # while foreign keys are enforced, which cascades over messages and sub-agent links.
    op.add_column(
        "sessions",
        sa.Column("kind", sa.String(16), nullable=False, server_default=sa.text("'chat'")),
    )
    op.create_index("ix_sessions_kind", "sessions", ["kind"])
    op.drop_index("ix_voice_requests_session_id", table_name="voice_requests")
    op.drop_index("ix_voice_requests_channel_id", table_name="voice_requests")
    op.drop_table("voice_requests")


def downgrade():
    raise RuntimeError("Downgrade is not supported for Sentinel database migrations.")
