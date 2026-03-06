---
sidebar_position: 4
title: Triggers
---

# Triggers

Triggers let you fire agent sessions on a schedule or in response to events — without manual intervention.

---

## Types

### Cron triggers
Calendar-based schedules using standard cron expressions.

```
0 9 * * MON-FRI    # Weekdays at 9am
0 */2 * * *        # Every 2 hours
0 0 * * *          # Daily at midnight
```

### Heartbeat triggers
Fixed-interval execution.

```
interval_seconds: 3600    # Every hour
interval_seconds: 300     # Every 5 minutes
```

---

## Routing

When a trigger fires, it routes a message to an agent session:

| Mode | Behavior |
|---|---|
| `main` | Routes to the default main session |
| `session` | Routes to a specific session by ID |

---

## What agents can do with triggers

Agents can manage their own triggers when permitted:

- Create new scheduled tasks
- Update trigger schedules or messages
- Enable/disable triggers
- Delete triggers they no longer need

This enables self-scheduling workflows — agents that set up their own follow-ups, recurring checks, and reminders.

---

## Common patterns

| Pattern | Cron |
|---|---|
| Daily morning briefing | `0 8 * * *` |
| Weekday standup | `0 9 * * MON-FRI` |
| Hourly health check | `0 * * * *` |
| Weekly lead scan | `0 9 * * MON` |
