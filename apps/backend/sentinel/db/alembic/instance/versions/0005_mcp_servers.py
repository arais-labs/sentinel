"""Add instance MCP connections and per-session tool loading."""

from alembic import op
import sqlalchemy as sa

revision = "0005_mcp_servers"
down_revision = "0004_voice_session"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mcp_servers",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("config", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("always_load", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tools", sa.JSON(), nullable=False),
    )
    op.create_table(
        "mcp_session_exposure",
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "server_id",
            sa.String(32),
            sa.ForeignKey("mcp_servers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("loaded_at", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("mcp_session_exposure")
    op.drop_table("mcp_servers")
