"""Initial Sentinel instance schema for fresh installations."""

from alembic import op

revision = "0001_instance_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE audit_logs (
	id CHAR(32) NOT NULL,
	timestamp DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	user_id VARCHAR(100),
	action VARCHAR(100) NOT NULL,
	resource_type VARCHAR(50),
	resource_id VARCHAR(100),
	request_summary JSON,
	status_code INTEGER,
	ip_address VARCHAR(45),
	request_id CHAR(32),
	duration_ms INTEGER,
	PRIMARY KEY (id)
)""")
    op.execute("""CREATE INDEX ix_audit_logs_action ON audit_logs (action)""")
    op.execute("""CREATE INDEX ix_audit_logs_timestamp ON audit_logs (timestamp)""")
    op.execute("""CREATE INDEX ix_audit_logs_user_id ON audit_logs (user_id)""")
    op.execute("""CREATE TABLE git_accounts (
	id CHAR(32) NOT NULL,
	name VARCHAR(120) NOT NULL,
	host VARCHAR(255) NOT NULL,
	scope_pattern VARCHAR(500) DEFAULT '*' NOT NULL,
	author_name VARCHAR(255) NOT NULL,
	author_email VARCHAR(320) NOT NULL,
	token TEXT NOT NULL,
	github_login VARCHAR(255),
	verified_at DATETIME,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id)
)""")
    op.execute("""CREATE INDEX ix_git_accounts_host ON git_accounts (host)""")
    op.execute("""CREATE UNIQUE INDEX ix_git_accounts_name ON git_accounts (name)""")
    op.execute("""CREATE TABLE modules (
	name VARCHAR NOT NULL,
	label VARCHAR NOT NULL,
	description TEXT DEFAULT '',
	icon VARCHAR DEFAULT 'box',
	fields JSON,
	fields_config JSON,
	actions JSON,
	secrets JSON,
	page_title VARCHAR,
	page_content TEXT,
	system BOOLEAN DEFAULT false NOT NULL,
	"order" INTEGER DEFAULT 100 NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY (name)
)""")
    op.execute("""CREATE TABLE permissions (
	action VARCHAR NOT NULL,
	level VARCHAR DEFAULT 'deny' NOT NULL,
	PRIMARY KEY (action)
)""")
    op.execute("""CREATE TABLE session_runtime_cleanup (
	session_id CHAR(32) NOT NULL,
	workspace_id CHAR(32) NOT NULL,
	directory TEXT NOT NULL,
	tools JSON NOT NULL,
	PRIMARY KEY (session_id)
)""")
    op.execute("""CREATE TABLE system_settings (
	"key" VARCHAR(100) NOT NULL,
	value TEXT NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY ("key")
)""")
    op.execute("""CREATE TABLE tool_approvals (
	id CHAR(32) NOT NULL,
	provider VARCHAR(40) DEFAULT 'tool' NOT NULL,
	tool_name VARCHAR(120) NOT NULL,
	session_id CHAR(32),
	action VARCHAR(160) NOT NULL,
	description TEXT,
	match_key TEXT,
	status VARCHAR(20) DEFAULT 'pending' NOT NULL,
	requested_by VARCHAR(120),
	decision_by VARCHAR(120),
	decision_note TEXT,
	payload_json JSON,
	result_json JSON,
	expires_at DATETIME NOT NULL,
	resolved_at DATETIME,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id)
)""")
    op.execute("""CREATE INDEX ix_tool_approvals_action ON tool_approvals (action)""")
    op.execute("""CREATE INDEX ix_tool_approvals_decision_by ON tool_approvals (decision_by)""")
    op.execute("""CREATE INDEX ix_tool_approvals_expires_at ON tool_approvals (expires_at)""")
    op.execute("""CREATE INDEX ix_tool_approvals_match_key ON tool_approvals (match_key)""")
    op.execute("""CREATE INDEX ix_tool_approvals_provider ON tool_approvals (provider)""")
    op.execute("""CREATE INDEX ix_tool_approvals_requested_by ON tool_approvals (requested_by)""")
    op.execute("""CREATE INDEX ix_tool_approvals_session_id ON tool_approvals (session_id)""")
    op.execute("""CREATE INDEX ix_tool_approvals_status ON tool_approvals (status)""")
    op.execute("""CREATE INDEX ix_tool_approvals_tool_name ON tool_approvals (tool_name)""")
    op.execute("""CREATE TABLE triggers (
	id CHAR(32) NOT NULL,
	user_id VARCHAR(100),
	name VARCHAR(255) NOT NULL,
	type VARCHAR(20) NOT NULL,
	enabled BOOLEAN DEFAULT true NOT NULL,
	config JSON NOT NULL,
	action_type VARCHAR(20) NOT NULL,
	action_config JSON NOT NULL,
	last_fired_at DATETIME,
	next_fire_at DATETIME,
	fire_count INTEGER DEFAULT 0 NOT NULL,
	error_count INTEGER DEFAULT 0 NOT NULL,
	consecutive_errors INTEGER DEFAULT 0 NOT NULL,
	last_error TEXT,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_triggers_type CHECK (type IN ('cron', 'webhook', 'heartbeat')),
	CONSTRAINT ck_triggers_action_type CHECK (action_type IN ('agent_message', 'tool_call', 'http_request'))
)""")
    op.execute("""CREATE INDEX ix_triggers_next_fire_at ON triggers (next_fire_at)""")
    op.execute("""CREATE INDEX ix_triggers_type ON triggers (type)""")
    op.execute("""CREATE INDEX ix_triggers_user_id ON triggers (user_id)""")
    op.execute("""CREATE TABLE workspaces (
	id CHAR(32) NOT NULL,
	name VARCHAR(120) NOT NULL,
	machine_id CHAR(32) NOT NULL,
	directory TEXT NOT NULL,
	distribution VARCHAR(32) DEFAULT 'alpine' NOT NULL,
	development_tools JSON DEFAULT '[]' NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (name)
)""")
    op.execute("""CREATE INDEX ix_workspaces_machine_id ON workspaces (machine_id)""")
    op.execute("""CREATE TABLE module_records (
	id VARCHAR NOT NULL,
	module_name VARCHAR NOT NULL,
	data JSON,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY (id),
	FOREIGN KEY(module_name) REFERENCES modules (name)
)""")
    op.execute("""CREATE TABLE module_secrets (
	module_name VARCHAR NOT NULL,
	"key" VARCHAR NOT NULL,
	value TEXT NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
	PRIMARY KEY (module_name, "key"),
	FOREIGN KEY(module_name) REFERENCES modules (name)
)""")
    op.execute("""CREATE TABLE sessions (
	id CHAR(32) NOT NULL,
	user_id VARCHAR(100) NOT NULL,
	agent_id VARCHAR(100),
	parent_session_id CHAR(32),
	workspace_id CHAR(32),
	title VARCHAR(255),
	initial_prompt TEXT,
	latest_system_prompt TEXT,
	status VARCHAR(20) DEFAULT 'active' NOT NULL,
	started_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	ended_at DATETIME,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	last_read_at DATETIME,
	conversation_message_count INTEGER DEFAULT 0 NOT NULL,
	last_auto_rename_count INTEGER DEFAULT 0 NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(parent_session_id) REFERENCES sessions (id) ON DELETE SET NULL,
	FOREIGN KEY(workspace_id) REFERENCES workspaces (id) ON DELETE RESTRICT
)""")
    op.execute("""CREATE INDEX ix_sessions_parent_session_id ON sessions (parent_session_id)""")
    op.execute("""CREATE INDEX ix_sessions_status ON sessions (status)""")
    op.execute("""CREATE INDEX ix_sessions_user_id ON sessions (user_id)""")
    op.execute("""CREATE INDEX ix_sessions_workspace_id ON sessions (workspace_id)""")
    op.execute("""CREATE TABLE trigger_logs (
	id CHAR(32) NOT NULL,
	trigger_id CHAR(32) NOT NULL,
	fired_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	status VARCHAR(20) NOT NULL,
	duration_ms INTEGER,
	input_payload JSON,
	output_summary TEXT,
	error_message TEXT,
	PRIMARY KEY (id),
	FOREIGN KEY(trigger_id) REFERENCES triggers (id) ON DELETE CASCADE
)""")
    op.execute("""CREATE INDEX ix_trigger_logs_trigger_id ON trigger_logs (trigger_id)""")
    op.execute("""CREATE TABLE memories (
	id CHAR(32) NOT NULL,
	content TEXT NOT NULL,
	title VARCHAR(200),
	summary TEXT,
	category VARCHAR(50) NOT NULL,
	parent_id CHAR(32),
	importance INTEGER DEFAULT 0 NOT NULL,
	pinned BOOLEAN DEFAULT false NOT NULL,
	is_system BOOLEAN DEFAULT false NOT NULL,
	system_key VARCHAR(100),
	embedding BLOB,
	metadata JSON DEFAULT '{}' NOT NULL,
	session_id CHAR(32),
	last_accessed_at DATETIME,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_memories_category CHECK (category IN ('core', 'preference', 'project', 'correction')),
	CONSTRAINT ck_memories_importance CHECK (importance >= 0 AND importance <= 100),
	CONSTRAINT ck_memories_system_key_consistency CHECK (((is_system AND system_key IS NOT NULL) OR (NOT is_system AND system_key IS NULL))),
	FOREIGN KEY(parent_id) REFERENCES memories (id) ON DELETE SET NULL,
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE SET NULL
)""")
    op.execute("""CREATE INDEX ix_memories_category ON memories (category)""")
    op.execute("""CREATE INDEX ix_memories_is_system ON memories (is_system)""")
    op.execute("""CREATE INDEX ix_memories_parent_id ON memories (parent_id)""")
    op.execute("""CREATE INDEX ix_memories_system_key ON memories (system_key)""")
    op.execute(
        """CREATE UNIQUE INDEX uq_memories_system_key ON memories (system_key) WHERE is_system"""
    )
    op.execute("""CREATE TABLE messages (
	id CHAR(32) NOT NULL,
	session_id CHAR(32) NOT NULL,
	role VARCHAR(20) NOT NULL,
	content TEXT NOT NULL,
	metadata JSON DEFAULT '{}' NOT NULL,
	token_count INTEGER,
	tool_call_id VARCHAR(100),
	tool_name VARCHAR(100),
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)""")
    op.execute("""CREATE INDEX ix_messages_session_id ON messages (session_id)""")
    op.execute("""CREATE TABLE session_action_grants (
	id CHAR(32) NOT NULL,
	session_id CHAR(32) NOT NULL,
	action VARCHAR(160) NOT NULL,
	approved_by VARCHAR(120) NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (session_id, action),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)""")
    op.execute(
        """CREATE INDEX ix_session_action_grants_session_id ON session_action_grants (session_id)"""
    )
    op.execute("""CREATE TABLE session_bindings (
	id CHAR(32) NOT NULL,
	user_id VARCHAR(100) NOT NULL,
	binding_type VARCHAR(40) NOT NULL,
	binding_key VARCHAR(255) NOT NULL,
	session_id CHAR(32) NOT NULL,
	is_active BOOLEAN DEFAULT true NOT NULL,
	metadata JSON DEFAULT '{}' NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)""")
    op.execute(
        """CREATE INDEX ix_session_bindings_binding_type ON session_bindings (binding_type)"""
    )
    op.execute("""CREATE INDEX ix_session_bindings_is_active ON session_bindings (is_active)""")
    op.execute("""CREATE INDEX ix_session_bindings_session_id ON session_bindings (session_id)""")
    op.execute("""CREATE INDEX ix_session_bindings_user_id ON session_bindings (user_id)""")
    op.execute(
        """CREATE INDEX ix_session_bindings_user_type_key ON session_bindings (user_id, binding_type, binding_key)"""
    )
    op.execute(
        """CREATE UNIQUE INDEX uq_session_bindings_active_route ON session_bindings (user_id, binding_type, binding_key) WHERE is_active"""
    )
    op.execute("""CREATE TABLE session_summaries (
	id CHAR(32) NOT NULL,
	session_id CHAR(32) NOT NULL,
	summary JSON NOT NULL,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)""")
    op.execute("""CREATE INDEX ix_session_summaries_session_id ON session_summaries (session_id)""")
    op.execute("""CREATE TABLE sub_agent_tasks (
	id CHAR(32) NOT NULL,
	session_id CHAR(32) NOT NULL,
	objective TEXT NOT NULL,
	constraints JSON DEFAULT '[]' NOT NULL,
	allowed_tools JSON DEFAULT '[]' NOT NULL,
	model VARCHAR(200),
	context TEXT,
	status VARCHAR(20) DEFAULT 'pending' NOT NULL,
	result JSON,
	tokens_used INTEGER DEFAULT 0 NOT NULL,
	turns_used INTEGER DEFAULT 0 NOT NULL,
	started_at DATETIME,
	completed_at DATETIME,
	created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_sub_agent_tasks_status CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
	FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE
)""")
    op.execute("""CREATE INDEX ix_sub_agent_tasks_session_id ON sub_agent_tasks (session_id)""")

    op.execute(
        """CREATE VIRTUAL TABLE memories_fts USING fts5(title, summary, content, content='memories', content_rowid='rowid', tokenize='porter unicode61')"""
    )
    op.execute(
        """CREATE TRIGGER memories_fts_insert AFTER INSERT ON memories BEGIN INSERT INTO memories_fts(rowid,title,summary,content) VALUES(new.rowid,new.title,new.summary,new.content); END"""
    )
    op.execute(
        """CREATE TRIGGER memories_fts_delete AFTER DELETE ON memories BEGIN INSERT INTO memories_fts(memories_fts,rowid,title,summary,content) VALUES('delete',old.rowid,old.title,old.summary,old.content); END"""
    )
    op.execute(
        """CREATE TRIGGER memories_fts_update AFTER UPDATE ON memories BEGIN INSERT INTO memories_fts(memories_fts,rowid,title,summary,content) VALUES('delete',old.rowid,old.title,old.summary,old.content); INSERT INTO memories_fts(rowid,title,summary,content) VALUES(new.rowid,new.title,new.summary,new.content); END"""
    )


def downgrade():
    raise RuntimeError("Downgrade is not supported for Sentinel database migrations.")
