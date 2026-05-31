"""Unify runtime storage.

Revision ID: 0002_unified_runtimes
Revises: 0001_runtime_ssh_targets
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_unified_runtimes"
down_revision = "0001_runtime_ssh_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_instances_runtime_target_id_runtime_ssh_targets", "instances", type_="foreignkey"
    )
    op.rename_table("runtime_ssh_targets", "runtimes")
    for column_name in (
        "host",
        "port",
        "username",
        "workspaces_dir",
        "auth_type",
        "encrypted_secret",
    ):
        op.alter_column("runtimes", column_name, nullable=True)
    op.alter_column("instances", "runtime_target_id", new_column_name="runtime_id")

    op.add_column("runtimes", sa.Column("provider", sa.String(length=32), nullable=True))
    op.add_column("runtimes", sa.Column("status", sa.String(length=32), nullable=True))
    op.add_column("runtimes", sa.Column("profile", sa.String(length=120), nullable=True))
    op.add_column(
        "runtimes", sa.Column("last_job_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("runtimes", sa.Column("last_job_status", sa.String(length=32), nullable=True))
    op.add_column(
        "runtimes",
        sa.Column(
            "provider_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "runtimes",
        sa.Column(
            "provider_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.execute("UPDATE runtimes SET provider = 'ssh', status = 'ready', profile = 'ssh'")
    op.alter_column("runtimes", "provider", nullable=False)
    op.alter_column("runtimes", "status", nullable=False)

    op.create_foreign_key(
        "fk_instances_runtime_id_runtimes",
        "instances",
        "runtimes",
        ["runtime_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_instances_runtime_id_runtimes", "instances", type_="foreignkey")
    op.drop_column("runtimes", "provider_state")
    op.drop_column("runtimes", "provider_config")
    op.drop_column("runtimes", "last_job_status")
    op.drop_column("runtimes", "last_job_id")
    op.drop_column("runtimes", "profile")
    op.drop_column("runtimes", "status")
    op.drop_column("runtimes", "provider")
    op.alter_column("instances", "runtime_id", new_column_name="runtime_target_id")
    op.rename_table("runtimes", "runtime_ssh_targets")
    op.create_foreign_key(
        "fk_instances_runtime_target_id_runtime_ssh_targets",
        "instances",
        "runtime_ssh_targets",
        ["runtime_target_id"],
        ["id"],
        ondelete="SET NULL",
    )
