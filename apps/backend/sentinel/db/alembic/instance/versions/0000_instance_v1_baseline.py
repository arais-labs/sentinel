"""instance v1 baseline

Revision ID: 0000_instance_v1
Revises:
Create Date: 2026-05-14
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision = "0000_instance_v1"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "permissions",
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("level", sa.String(), server_default=sa.text("'deny'"), nullable=False),
        sa.PrimaryKeyConstraint("action"),
    )
    op.create_table(
        "modules",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=True),
        sa.Column("icon", sa.String(), server_default=sa.text("'box'"), nullable=True),
        sa.Column("fields", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("fields_config", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("actions", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("secrets", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("page_title", sa.String(), nullable=True),
        sa.Column("page_content", sa.Text(), nullable=True),
        sa.Column("system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("order", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=50), nullable=True),
        sa.Column("resource_id", sa.String(length=100), nullable=True),
        sa.Column("request_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "git_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("scope_pattern", sa.String(length=500), server_default=sa.text("'*'"), nullable=False),
        sa.Column("author_name", sa.String(length=255), nullable=False),
        sa.Column("author_email", sa.String(length=320), nullable=False),
        sa.Column("token_read", sa.Text(), nullable=False),
        sa.Column("token_write", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("agent_id", sa.String(length=100), nullable=True),
        sa.Column("parent_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("initial_prompt", sa.Text(), nullable=True),
        sa.Column("latest_system_prompt", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_message_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_auto_rename_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(["parent_session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "triggers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("action_type", sa.String(length=20), nullable=False),
        sa.Column("action_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fire_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("consecutive_errors", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("action_type IN ('agent_message', 'tool_call', 'http_request')", name="ck_triggers_action_type"),
        sa.CheckConstraint("type IN ('cron', 'webhook', 'heartbeat')", name="ck_triggers_type"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tool_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=40), server_default=sa.text("'tool'"), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("match_key", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("requested_by", sa.String(length=120), nullable=True),
        sa.Column("decision_by", sa.String(length=120), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "module_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("module_name", sa.String(), nullable=False),
        sa.Column("data", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["module_name"], ["modules.name"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "module_secrets",
        sa.Column("module_name", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["module_name"], ["modules.name"]),
        sa.PrimaryKeyConstraint("module_name", "key"),
    )
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("tool_call_id", sa.String(length=100), nullable=True),
        sa.Column("tool_name", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("importance", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pinned", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("system_key", sa.String(length=100), nullable=True),
        sa.Column("embedding", Vector(dim=1536), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("((is_system AND system_key IS NOT NULL) OR (NOT is_system AND system_key IS NULL))", name="ck_memories_system_key_consistency"),
        sa.CheckConstraint("category IN ('core', 'preference', 'project', 'correction')", name="ck_memories_category"),
        sa.CheckConstraint("importance >= 0 AND importance <= 100", name="ck_memories_importance"),
        sa.ForeignKeyConstraint(["parent_id"], ["memories.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "session_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw_token_count", sa.Integer(), nullable=False),
        sa.Column("compressed_token_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "session_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.String(length=100), nullable=False),
        sa.Column("binding_type", sa.String(length=40), nullable=False),
        sa.Column("binding_key", sa.String(length=255), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sub_agent_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("constraints", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("allowed_tools", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("max_turns", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("max_tokens", sa.Integer(), server_default=sa.text("50000"), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default=sa.text("300"), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tokens_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("turns_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'running', 'completed', 'failed', 'cancelled')", name="ck_sub_agent_tasks_status"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "trigger_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["trigger_id"], ["triggers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
    op.create_index("ix_git_accounts_host", "git_accounts", ["host"])
    op.create_index("ix_git_accounts_name", "git_accounts", ["name"], unique=True)
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_index("ix_memories_category", "memories", ["category"])
    op.create_index("ix_memories_is_system", "memories", ["is_system"])
    op.create_index("ix_memories_parent_id", "memories", ["parent_id"])
    op.create_index("ix_memories_system_key", "memories", ["system_key"])
    op.create_index(
        "uq_memories_system_key",
        "memories",
        ["system_key"],
        unique=True,
        postgresql_where=sa.text("is_system"),
    )
    op.create_index("ix_session_bindings_binding_type", "session_bindings", ["binding_type"])
    op.create_index("ix_session_bindings_is_active", "session_bindings", ["is_active"])
    op.create_index("ix_session_bindings_session_id", "session_bindings", ["session_id"])
    op.create_index("ix_session_bindings_user_id", "session_bindings", ["user_id"])
    op.create_index("ix_session_bindings_user_type_key", "session_bindings", ["user_id", "binding_type", "binding_key"])
    op.create_index("ix_session_summaries_session_id", "session_summaries", ["session_id"])
    op.create_index("ix_sessions_parent_session_id", "sessions", ["parent_session_id"])
    op.create_index("ix_sessions_status", "sessions", ["status"])
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sub_agent_tasks_session_id", "sub_agent_tasks", ["session_id"])
    op.create_index("ix_tool_approvals_action", "tool_approvals", ["action"])
    op.create_index("ix_tool_approvals_decision_by", "tool_approvals", ["decision_by"])
    op.create_index("ix_tool_approvals_expires_at", "tool_approvals", ["expires_at"])
    op.create_index("ix_tool_approvals_match_key", "tool_approvals", ["match_key"])
    op.create_index("ix_tool_approvals_provider", "tool_approvals", ["provider"])
    op.create_index("ix_tool_approvals_requested_by", "tool_approvals", ["requested_by"])
    op.create_index("ix_tool_approvals_session_id", "tool_approvals", ["session_id"])
    op.create_index("ix_tool_approvals_status", "tool_approvals", ["status"])
    op.create_index("ix_tool_approvals_tool_name", "tool_approvals", ["tool_name"])
    op.create_index("ix_trigger_logs_trigger_id", "trigger_logs", ["trigger_id"])
    op.create_index("ix_triggers_next_fire_at", "triggers", ["next_fire_at"])
    op.create_index("ix_triggers_type", "triggers", ["type"])
    op.create_index("ix_triggers_user_id", "triggers", ["user_id"])
    op.create_index(
        "uq_session_bindings_active_main",
        "session_bindings",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active AND binding_type = 'main'"),
    )
    op.create_index(
        "uq_session_bindings_active_route",
        "session_bindings",
        ["user_id", "binding_type", "binding_key"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_content_tsv "
        "ON memories USING GIN (to_tsvector('english', content))"
    )
    op.create_index(
        "idx_memories_roots_rank",
        "memories",
        ["parent_id", sa.text("pinned DESC"), sa.text("importance DESC"), sa.text("updated_at DESC")],
    )
    op.execute(
        """
        DO $$
        BEGIN
            CREATE INDEX IF NOT EXISTS idx_memories_embedding_ivfflat
            ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
        EXCEPTION
            WHEN OTHERS THEN
                RAISE NOTICE 'Skipping idx_memories_embedding_ivfflat: %', SQLERRM;
        END
        $$;
        """
    )



def downgrade() -> None:
    raise RuntimeError("Downgrade is not supported for the instance v1 baseline.")
