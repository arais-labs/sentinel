-- Add last_read_at column to sessions for unread message tracking.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS last_read_at TIMESTAMPTZ;

-- Backfill: set last_read_at to the latest message timestamp per session
-- so existing sessions don't appear as unread.
UPDATE sessions
SET last_read_at = sub.latest_msg
FROM (
    SELECT session_id, MAX(created_at) AS latest_msg
    FROM messages
    GROUP BY session_id
) sub
WHERE sessions.id = sub.session_id
  AND sessions.last_read_at IS NULL;
