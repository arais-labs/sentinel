CREATE INDEX IF NOT EXISTS idx_memories_roots_rank
ON memories(parent_id, pinned DESC, importance DESC, updated_at DESC);
