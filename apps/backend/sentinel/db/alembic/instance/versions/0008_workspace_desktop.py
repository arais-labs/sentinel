"""Desktop selection is independent of development tools."""

from alembic import op
import sqlalchemy as sa

revision = "0008_workspace_desktop"
down_revision = "0007_workspace_name_cache"
branch_labels = None
depends_on = None


def upgrade():
    # ADD COLUMN does not rebuild this referenced table or trigger FK actions.
    if "desktop" not in {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("workspaces")
    }:
        op.add_column(
            "workspaces", sa.Column("desktop", sa.String(16), nullable=False, server_default="none")
        )
    op.execute("""
        UPDATE workspaces
        SET desktop = 'xfce',
            development_tools = (
                SELECT json_group_array(value) FROM json_each(workspaces.development_tools)
                WHERE value != 'desktop'
            )
        WHERE EXISTS (
            SELECT 1 FROM json_each(workspaces.development_tools) WHERE value = 'desktop'
        )
    """)


def downgrade():
    # A Wayland selection cannot be represented by the old schema.
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM workspaces WHERE desktop = 'weston' LIMIT 1"))
        .first()
    ):
        raise RuntimeError("Choose XFCE or None before downgrading desktop settings")
    op.execute("""
        UPDATE workspaces SET development_tools = json_insert(development_tools, '$[#]', 'desktop')
        WHERE desktop = 'xfce'
    """)
    op.drop_column("workspaces", "desktop")
