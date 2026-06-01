"""remove main session bindings

Revision ID: 0001_remove_main_session_bindings
Revises: 0000_instance_v1
Create Date: 2026-06-01
"""

from __future__ import annotations

from alembic import op

revision = "0001_remove_main_session_bindings"
down_revision = "0000_instance_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_session_bindings_active_main")
    op.execute("DELETE FROM session_bindings WHERE binding_type = 'main'")


def downgrade() -> None:
    raise RuntimeError("Downgrade is not supported for Sentinel database migrations.")
