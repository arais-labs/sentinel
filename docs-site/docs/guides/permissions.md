---
sidebar_position: 3
title: Permissions
---

# Permissions

Sentinel gates every module action through a three-level permission system. A
permission maps a module action string (`<module>.<action>`) to one level.

| Level | Meaning |
|---|---|
| `allow` | Execute the action immediately |
| `approval` | Create a pending approval record and return `202 Accepted` |
| `deny` | Block the action and return `403 Forbidden` |

These are the only valid levels (`allow`, `approval`, `deny`).

## Permissions are per-instance

Sentinel runs **multiple logical instances** in one deployment. Each instance
has its own application database, and the `permissions` table lives **inside the
instance database** — not the shared manager database. Setting a level for an
action affects only that one instance; another instance keeps its own,
independent permission map.

When you manage permissions through the API or UI, requests are scoped to an
instance via the route prefix `/api/v1/instances/{instance_name}/...`. See
[Multi-instance](./multi-instance.md) for how instance scoping works.

## How a level is resolved

When the agent invokes a module action, Sentinel looks up the action key
(for example `tasks.delete`) in the instance's `permissions` table:

1. **A row exists** with a valid level (`allow`/`approval`/`deny`) → that level
   is used.
2. **No row exists** (or the stored value is invalid) → the action's **declared
   default** is used.

The declared default is **not** universally `allow`. It comes from the module
definition:

- **System modules** default to `approval` if the action is marked
  approval-gated, otherwise `allow`. These defaults are merged from
  `combined_agent_permissions()`, which seeds the static `AGENT_PERMISSIONS` map
  plus each system module's per-action `approval` flag.
- **Dynamic (user-defined) modules** auto-generate CRUD actions
  (`list_records`, `get_record`, `create_records`, `update_records`,
  `delete_records`, `get_page`, `edit_page`) with built-in defaults. Read and
  create actions default to `allow`; `delete_records` and `edit_page` default to
  `approval`. Each action's level can be overridden when the module is created
  or updated.

In short: an action with no explicit row falls back to its module-declared
default — which is `approval` for destructive/sensitive actions and `allow` for
benign ones.

## The model in code

Permissions are stored as plain rows with exactly two columns:

- `action` — the action key, e.g. `tasks.delete`, `modules.create`, or a dynamic
  module action like `notes.delete_records` (primary key)
- `level` — one of `allow`, `approval`, `deny`

There is **no separate resource column and no ordered wildcard rule engine** —
each row maps one literal action string to one level. The table's column default
is `deny`, but in practice levels are seeded from module defaults at startup and
when modules are created, so unseeded actions resolve through the declared
default described above rather than a blanket deny.

## Seeded system defaults

The static defaults seeded on startup include:

| Action | Default level |
|---|---|
| `tasks.list` / `tasks.create` / `tasks.update` | `allow` |
| `tasks.delete` | `approval` |
| `documents.create` / `documents.update` / `documents.delete` | `approval` |
| `approvals.list` / `approvals.create` | `allow` |
| `approvals.resolve` | `deny` |
| `modules.list` | `allow` |
| `modules.create` / `modules.update` / `modules.delete` | `approval` |
| `settings.manage` | `deny` |

System-module actions (browser, runtime, git, memory, sub-agents, etc.) are
merged on top of these with `approval` where the action declares it, otherwise
`allow`.

## A hardening baseline

If you want stricter gating, set these explicitly per instance:

1. `modules.create` → `approval`
2. `modules.update` → `approval`
3. `modules.delete` → `approval`
4. `approvals.resolve` → `deny` (the agent should never resolve its own approvals)
5. Sensitive tool/runtime actions → `approval`

## Approval handling reminder

When an action's permission is `approval`, the API returns **`202 Accepted`**
with an approval payload — this is **not an error**. The action is pending until
a human resolves it (approve or reject). A `403 Forbidden` means the action was
denied outright.

For how the agent handles a pending `202` and the approval lifecycle, see
[Approvals](../concepts/approvals.md).

## Related

- [Modules & permissions](../concepts/modules-and-permissions.md) — system vs.
  dynamic modules and how the registry works
- [Creating modules](./creating-modules.md) — defining actions and their
  permission defaults
- [Approvals](../concepts/approvals.md) — the `202` contract and approval states
