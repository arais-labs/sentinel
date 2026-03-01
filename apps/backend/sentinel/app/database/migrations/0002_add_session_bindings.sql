-- Canonical session routing pointers (main + channel bindings).
CREATE TABLE IF NOT EXISTS session_bindings (
    id UUID PRIMARY KEY,
    user_id VARCHAR(100) NOT NULL,
    binding_type VARCHAR(40) NOT NULL,
    binding_key VARCHAR(255) NOT NULL,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_session_bindings_user_id ON session_bindings(user_id);
CREATE INDEX IF NOT EXISTS ix_session_bindings_binding_type ON session_bindings(binding_type);
CREATE INDEX IF NOT EXISTS ix_session_bindings_is_active ON session_bindings(is_active);
CREATE INDEX IF NOT EXISTS ix_session_bindings_session_id ON session_bindings(session_id);
CREATE INDEX IF NOT EXISTS ix_session_bindings_user_type_key ON session_bindings(user_id, binding_type, binding_key);

-- Guarantee one active route per key and one active main per user.
CREATE UNIQUE INDEX IF NOT EXISTS uq_session_bindings_active_route
    ON session_bindings(user_id, binding_type, binding_key)
    WHERE is_active;

CREATE UNIQUE INDEX IF NOT EXISTS uq_session_bindings_active_main
    ON session_bindings(user_id)
    WHERE is_active AND binding_type = 'main';
