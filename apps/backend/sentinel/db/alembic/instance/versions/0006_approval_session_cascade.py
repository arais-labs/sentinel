"""Remove orphaned approvals and tie session approvals to their session."""

from alembic import op

revision = "0006_approval_session_cascade"
down_revision = "0005_mcp_servers"
branch_labels = None
depends_on = None


def upgrade():
    # Sessionless approvals are valid; only remove references to deleted sessions.
    op.execute(
        "DELETE FROM tool_approvals "
        "WHERE session_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM sessions WHERE sessions.id = tool_approvals.session_id)"
    )
    with op.batch_alter_table("tool_approvals") as batch:
        batch.create_foreign_key(
            "fk_tool_approvals_session_id_sessions",
            "sessions",
            ["session_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade():
    # The constraint is reversible; deleted orphan records are not restored.
    with op.batch_alter_table("tool_approvals") as batch:
        batch.drop_constraint("fk_tool_approvals_session_id_sessions", type_="foreignkey")
