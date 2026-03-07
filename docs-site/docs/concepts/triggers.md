---
sidebar_position: 5
title: Triggers
---

# Triggers

Triggers fire agent turns automatically — on a schedule or at a fixed interval — without manual input. Agents can also create, update, and delete their own triggers when permitted.

---

## Trigger types

### Cron triggers
Calendar-based schedules using standard 5-field cron expressions.

```
0 9 * * MON-FRI     # Weekdays at 9am
0 */2 * * *         # Every 2 hours
0 0 * * *           # Daily at midnight
0 9 * * MON         # Weekly on Monday at 9am
```

### Heartbeat triggers
Fixed-interval execution independent of calendar time.

```
interval_seconds: 3600    # Every hour
interval_seconds: 300     # Every 5 minutes
interval_seconds: 1800    # Every 30 minutes
```

### Webhook triggers
HTTP endpoint based trigger type is also supported at API level for external inbound events.


---

## Routing: where the message goes

When a trigger fires, it routes a message to a specific agent session.

| Mode | Behavior |
|---|---|
| `main` | Routes to the canonical main session for the user |
| `session` | Routes to a specific session by its ID |

Set `route_mode` and `target_session_id` in `action_config` when creating the trigger.

### Routing fallback behavior

:::warning Silent fallback
If `route_mode=session` is set and the `target_session_id` no longer exists (the session was deleted), the trigger **silently falls back to the main session**. No error is raised. The trigger continues to fire.

If you see trigger messages appearing in the main session unexpectedly, check whether the original target session still exists. The trigger's stored `action_config` will contain a `route_fallback_reason` field after fallback occurs.
:::

### Legacy `session_id` field

Older trigger configs may use `session_id` instead of `target_session_id`. The system treats this as a `session`-mode target for backward compatibility. If you set `route_mode=main` but also include a `session_id`, the system will automatically promote the route mode to `session` and use that ID as the target.

---

## Action config structure

```json
{
  "message": "Run the weekly competitor scan and summarize findings.",
  "route_mode": "session",
  "target_session_id": "abc123-...",
}
```

After routing resolves, the stored config is updated with:

| Field | Value |
|---|---|
| `resolved_session_id` | The session the message was actually sent to |
| `route_fallback_reason` | Set if fallback occurred (`missing_target_session`, `invalid_or_deleted_target_session`) |
| `last_invalid_target_session_id` | The session ID that was invalid, for debugging |

---

## What agents can do with triggers

Agents can manage triggers programmatically via the `trigger_create`, `trigger_update`, `trigger_list`, and `trigger_delete` tools — when those tools are available in the current context.

This enables self-scheduling workflows: agents that set up their own recurring checks, periodic reports, follow-up reminders, and cleanup jobs.

---

## Common patterns

| Pattern | Type | Config |
|---|---|---|
| Daily morning briefing | Cron | `0 8 * * *` |
| Weekday standup | Cron | `0 9 * * MON-FRI` |
| Hourly health check | Heartbeat | `interval_seconds: 3600` |
| Weekly lead scan | Cron | `0 9 * * MON` |
| Every 30 min monitoring | Heartbeat | `interval_seconds: 1800` |
| Monthly report | Cron | `0 9 1 * *` |

---

## Trigger lifecycle

1. Trigger fires at the scheduled time
2. Routing resolves to a session (with fallback if needed)
3. Agent message is injected into the resolved session
4. Agent runs the turn normally — same loop, same tools, same memory
5. Trigger waits for next scheduled time
