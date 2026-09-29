"""Browser selection is independent of desktop and development tools."""

from alembic import op
import sqlalchemy as sa

revision = "0009_workspace_browser"
down_revision = "0008_workspace_desktop"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workspaces", sa.Column("browser", sa.String(16), nullable=False, server_default="chromium")
    )


def downgrade():
    op.drop_column("workspaces", "browser")
