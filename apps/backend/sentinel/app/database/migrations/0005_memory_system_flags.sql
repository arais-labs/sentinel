ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE memories
    ADD COLUMN IF NOT EXISTS system_key VARCHAR(100);

UPDATE memories
SET
    is_system = FALSE,
    system_key = NULL
WHERE
    NOT is_system;

WITH ranked AS (
    SELECT
        id,
        title,
        ROW_NUMBER() OVER (
            PARTITION BY title
            ORDER BY created_at ASC, id ASC
        ) AS rn
    FROM memories
    WHERE
        parent_id IS NULL
        AND category = 'core'
        AND title IN ('Agent Identity', 'User Profile')
)
UPDATE memories AS m
SET
    is_system = TRUE,
    system_key = CASE
        WHEN m.title = 'Agent Identity' THEN 'agent_identity'
        WHEN m.title = 'User Profile' THEN 'user_profile'
        ELSE NULL
    END,
    pinned = TRUE
FROM ranked AS r
WHERE
    m.id = r.id
    AND r.rn = 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_memories_system_key_consistency'
    ) THEN
        ALTER TABLE memories
        ADD CONSTRAINT ck_memories_system_key_consistency
        CHECK (((is_system AND system_key IS NOT NULL) OR (NOT is_system AND system_key IS NULL)));
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_system_key
ON memories(system_key)
WHERE is_system;
