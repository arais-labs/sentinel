-- Remove unsupported "event" trigger type.
-- Legacy event triggers are converted to disabled webhooks so rows remain valid.
UPDATE triggers
SET
    type = 'webhook',
    enabled = FALSE,
    next_fire_at = NULL,
    config = '{}'::jsonb
WHERE type = 'event';

ALTER TABLE triggers DROP CONSTRAINT IF EXISTS ck_triggers_type;
ALTER TABLE triggers
ADD CONSTRAINT ck_triggers_type
CHECK (type IN ('cron', 'webhook', 'heartbeat'));
