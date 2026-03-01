DO $$
BEGIN
    CREATE INDEX IF NOT EXISTS idx_memories_embedding_ivfflat
    ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
EXCEPTION
    WHEN OTHERS THEN
        -- IVFFlat may fail when pgvector index preconditions are not met yet.
        RAISE NOTICE 'Skipping idx_memories_embedding_ivfflat: %', SQLERRM;
END
$$;
