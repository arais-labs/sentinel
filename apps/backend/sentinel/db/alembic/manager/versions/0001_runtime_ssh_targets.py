"""runtime ssh targets

Revision ID: 0001_runtime_ssh_targets
Revises: 0000_manager_v1
Create Date: 2026-05-22
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_runtime_ssh_targets"
down_revision = "0000_manager_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_ssh_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("workspaces_dir", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.String(length=24), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.add_column("instances", sa.Column("runtime_target_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_instances_runtime_target_id_runtime_ssh_targets",
        "instances",
        "runtime_ssh_targets",
        ["runtime_target_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    raise RuntimeError("Downgrade is not supported for manager migrations.")
