CREATE INDEX IF NOT EXISTS idx_memories_content_tsv
ON memories USING GIN (to_tsvector('english', content));
