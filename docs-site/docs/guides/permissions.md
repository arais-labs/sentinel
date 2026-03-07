---
sidebar_position: 3
title: Permissions
---

# Permissions

Permissions in araiOS map an action string to one level.

Levels:

| Level | Meaning |
|---|---|
| `allow` | Execute immediately |
| `approval` | Create pending approval and return `202` |
| `deny` | Block and return `403` |

Default for unknown actions is `allow`.

---

## Actual model in code

Permissions are stored as rows with:

- `action` example: `tasks.create`, `modules.create`, `slack.send_message`
- `level` one of `allow`, `approval`, `deny`

There is no separate resource column and no ordered wildcard rule engine in araiOS permission storage.

---

## Common hardening baseline

1. `modules.create` -> `approval`
2. `modules.update` -> `approval`
3. `modules.delete` -> `approval`
4. `approvals.resolve` -> `deny` for agent role
5. Sensitive tool actions -> `approval`

---

## Approval handling reminder

When permission is `approval`, APIs return `202` with approval payload. This is not an error.
