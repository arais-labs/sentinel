---
sidebar_position: 1
title: API Reference
---

# API Reference

Sentinel uses FastAPI over a private Unix socket managed by Electron. The
desktop app hosts **multiple logical instances**, so almost every feature route is
scoped by an instance name in the path:

```
/api/v1/instances/{instance_name}/...
```

A handful of routes are **manager-scoped** (not tied to a single instance):
instance management, machines, and application-level configuration.

:::tip Start here
- `GET /api/v1/version` — identify the running build.
- `GET /api/v1/instances` — list the instances on this deployment.
- `GET /api/v1/instances/{instance_name}/modules` — discover the module catalog.
- `GET /api/v1/instances/{instance_name}/permissions` — inspect action policy.
:::

---

## Desktop transport and scoping

The React renderer sends requests through Electron's desktop bridge. Electron
forwards them to FastAPI over a private Unix socket; the backend does not listen
on a TCP port. HTTP route semantics remain unchanged inside that transport.
Sentinel has no user accounts, login, session cookies, or user roles. Provider
credentials and remote-runtime authentication remain configured separately.

Instance-scoped routes resolve the target instance from the
`{instance_name}` path segment. The name is normalized (lowercase
alphanumeric + dash, 1–80 chars) and each instance is backed by its own
application database, runtime context, tool registry, and LLM provider
credentials.

:::note Per-instance LLM credentials
LLM provider credentials (Anthropic / OpenAI / Gemini / etc.) are **not** read
from environment variables. They live encrypted in each instance's
`system_settings` and are managed through
`POST /api/v1/instances/{instance_name}/settings/api-keys`.
:::

---

## Manager-scoped routes

### Instances — `/api/v1/instances`

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/instances` | List instances |
| `POST` | `/api/v1/instances` | Create an instance (creates its database, runs instance migrations, seeds defaults) |
| `GET` | `/api/v1/instances/{name}` | Get one instance |
| `PATCH` | `/api/v1/instances/{name}` | Update an instance (rebuilds its runtime context) |
| `POST` | `/api/v1/instances/{name}/rename` | Rename an instance (rebuilds context; the underlying database name is **not** renamed) |
| `DELETE` | `/api/v1/instances/{name}` | Delete an instance (`204`) |

:::note
Deleting an instance removes its SQLite database and attachments directory after
closing its database connections. Export a backup first if needed.
:::

### Machines — `/api/v1/machines`

Local or SSH machines registered in the manager database. Workspaces
reference a machine; instances do not select a machine directly.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/machines` | List machines |
| `POST` | `/api/v1/machines` | Create a machine |
| `GET` | `/api/v1/machines/capabilities` | Supported machine capabilities |
| `GET` | `/api/v1/machines/{machine_id}` | Get one machine |
| `PATCH` | `/api/v1/machines/{machine_id}` | Update a machine |
| `DELETE` | `/api/v1/machines/{machine_id}` | Delete a machine (`204`) |
| `POST` | `/api/v1/machines/{machine_id}/{action}` | Run an action (`start` / `stop` / `test` …) |
| `POST` | `/api/v1/machines/test` | Test a connection without persisting |
| `GET` | `/api/v1/machines/jobs/{job_id}` | Poll an async machine job |

### Workspaces — instance-scoped

Paths below use `/api/v1/instances/{instance_name}`.

| Method | Path | Description |
|---|---|---|
| `GET`, `POST` | `/workspaces` | List or register named directories on machines |
| `PATCH` | `/workspaces/{id}` | Rename a workspace |
| `DELETE` | `/workspaces/{id}` | Remove an unattached registration; retain files |
| `GET` | `/sessions/{id}/workspace` | Get the session attachment, or null |
| `PUT` | `/sessions/{id}/workspace` | Attach or detach using `workspace_id` (null to detach) |

New sessions start unattached. Attachments cannot change while the agent or its
terminal commands are running. Sub-agents inherit their parent's attachment.
Machine deletion or rebuilding is blocked while workspaces reference it.

### System

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/version` | Build identity: `{ version, commit, channel }` (commit/channel are `null` when run from source) |
| `GET` | `/health` | Liveness |
| `GET` | `/health/ready` | Readiness probe |

---

## Instance-scoped routes

All of the following are prefixed with `/api/v1/instances/{instance_name}`.

### Sessions

| Method | Path | Description |
|---|---|---|
| `GET` | `/sessions` | List sessions |
| `POST` | `/sessions` | Create a session |
| `GET` | `/sessions/{id}` | Get a session |
| `PATCH` | `/sessions/{id}` | Update a session |
| `DELETE` | `/sessions/{id}` | Delete a session (cascades messages, sub-agent tasks, summaries) |
| `POST` | `/sessions/{id}/chat` | Run the agent for one turn |
| `POST` | `/sessions/{id}/stop` | Cancel the active agent run |
| `GET` | `/sessions/{id}/messages` | List messages |
| `POST` | `/sessions/{id}/messages` | Post a user message |
| `POST` | `/sessions/{id}/messages/{message_id}/retry` | Retry from a message |
| `POST` | `/sessions/{id}/read` | Mark read |
| `GET` | `/sessions/{id}/context-usage` | Token budget / usage metrics |

Machine workbench (file explorer, git diff, port forwarding) hangs off the same
session prefix:

| Method | Path | Description |
|---|---|---|
| `GET` | `/sessions/{id}/runtime/files` | List workspace files |
| `GET` | `/sessions/{id}/runtime/file` | Preview a file |
| `GET` | `/sessions/{id}/runtime/download` | Download a file |
| `GET` | `/sessions/{id}/runtime/git/roots` | Git repo roots in the workspace |
| `GET` | `/sessions/{id}/runtime/git/changed` | Changed files |
| `GET` | `/sessions/{id}/runtime/git/diff` | Diff (with context lines) |
| `DELETE` | `/sessions/{id}/terminals/{terminal_id}` | Close a terminal |

### Memory

| Method | Path | Description |
|---|---|---|
| `GET` | `/memory` | List memory items |
| `POST` | `/memory` | Create a memory node |
| `POST` | `/memory/search` | Hybrid search (vector → keyword → substring → recent fallback) |
| `GET` | `/memory/roots` | List root nodes |
| `GET` | `/memory/nodes/{id}` | Get a node |
| `GET` | `/memory/nodes/{id}/children` | List children |
| `PATCH` | `/memory/nodes/{id}` | Update a node |
| `POST` | `/memory/nodes/{id}/touch` | Update access time |
| `GET` | `/memory/stats` | Memory statistics |
| `DELETE` | `/memory/{id}` | Delete a node |

### Triggers

| Method | Path | Description |
|---|---|---|
| `GET` | `/triggers` | List triggers |
| `POST` | `/triggers` | Create a trigger (cron or heartbeat) |
| `GET` | `/triggers/{id}` | Get a trigger |
| `PATCH` | `/triggers/{id}` | Update a trigger |
| `DELETE` | `/triggers/{id}` | Delete a trigger |
| `POST` | `/triggers/{id}/fire` | Fire a trigger manually |
| `GET` | `/triggers/{id}/logs` | Execution history |

### Settings

| Method | Path | Description |
|---|---|---|
| `POST` | `/settings/api-keys` | Set LLM provider credentials (stored encrypted) |
| `GET` | `/settings/api-keys/status` | Which provider keys are configured |
| `DELETE` | `/settings/api-keys` | Clear provider credentials |
| `POST` | `/settings/primary-provider` | Set the primary provider |
| `GET` | `/settings/logging` | Current logging levels |
| `POST` | `/settings/logging/levels` | Override logging levels |
| `DELETE` | `/settings/logging/levels` | Remove a logging override |
| `POST` | `/settings/logging/reset` | Reset logging levels |

### Backup / restore

| Method | Path | Description |
|---|---|---|
| `GET` | `/backup/items` | Selectable item categories (`sessions`, `memories`, `modules`, `triggers`) |
| `POST` | `/backup/export` | Export an encrypted, passphrase-protected backup |
| `POST` | `/backup/inspect` | Inspect a backup file (version, items) |
| `POST` | `/backup/import` | Restore a validated backup (rebuilds the runtime context if `modules` are imported) |

Restore accepts the supported payload format (`schema_version: 3`) and validates
its contents after decryption. The originating app's `created_by_version` is
informational, not an app-version compatibility gate. Invalid or unsupported
payloads are rejected during inspection/unlock; older backup formats are not
supported. Restores do not depend on a manually pinned Alembic revision.

Backups are encrypted with AES-GCM using a scrypt-derived key from the passphrase,
which is required for both export and import.

### Git

| Method | Path | Description |
|---|---|---|
| `GET` | `/git/...` | Read-only repo inspection (roots, changed files, diffs) |

Pushes to `main` are blocked at the runtime layer.

### Telegram

| Method | Path | Description |
|---|---|---|
| `GET` | `/telegram/status` | Bridge status |
| `POST` | `/telegram/configure` | Set the bot token |
| `DELETE` | `/telegram/configure` | Remove the bot token |
| `POST` | `/telegram/start` | Start the bridge |
| `POST` | `/telegram/stop` | Stop the bridge |
| `POST` | `/telegram/owner` | Bind the owner chat |
| `DELETE` | `/telegram/owner` | Unbind the owner chat |

Telegram is configured per instance — each instance runs its own bot. See the
[Telegram guide](../guides/telegram.md).

---

## Permissions and approvals

### Permissions

| Method | Path | Description |
|---|---|---|
| `GET` | `/permissions` | List all permission actions and their levels |
| `PATCH` | `/permissions/{action}` | Set the level for one action (`allow` / `approval` / `deny`) |

Sentinel uses a three-level permission model per action:

- **allow** — execute normally
- **approval** — create an approval record and return `202`
- **deny** — refuse and return `403`

### Approvals

Approval records are created by the agent runtime when an action's level is
`approval`; there is no generic "create approval" endpoint. Resolve them by
the `provider` returned in the approval record (the requesting tool or module
identifier), together with its approval ID. Do not substitute a category name.

| Method | Path | Description |
|---|---|---|
| `GET` | `/approvals` | List approvals (filter: `?status=pending\|approved\|rejected`) |
| `POST` | `/approvals/{provider}/{approval_id}/approve` | Approve a pending approval |
| `POST` | `/approvals/{provider}/{approval_id}/reject` | Reject a pending approval |

#### Approval response codes

| Code | Meaning |
|---|---|
| `202` | Action requires approval and was queued as pending |
| `403` | Action denied by policy |
| `404` | Approval id / provider not found |

:::note 202 is not a failure
Approval-gated actions return `202 Accepted` (pending), not `403`. The agent must
treat `202` as "wait for resolution," not as an error. Approval state transitions:
`pending → approved | rejected | timed_out | cancelled`.
:::

---

## Modules

Modules are the agent's tools and data surfaces. The instance catalog combines
built-in modules with custom modules. Use `GET /modules` to discover the current
catalog and action schemas instead of hard-coding a fixed list.

### Module registry

| Method | Path | Description |
|---|---|---|
| `GET` | `/modules` | List modules (system + custom) |
| `POST` | `/modules` | Create a custom module (`201`) |
| `POST` | `/modules/import` | Import a module package (with optional seed records / permissions) |
| `GET` | `/modules/{name}` | Get module config |
| `PATCH` | `/modules/{name}` | Update a module |
| `DELETE` | `/modules/{name}` | Delete a module |

### Records (data modules)

| Method | Path | Description |
|---|---|---|
| `GET` | `/modules/{name}/records` | List records (optional `?filter_field=&filter_value=`) |
| `POST` | `/modules/{name}/records` | Create a record (`201`) |
| `GET` | `/modules/{name}/records/{record_id}` | Get one record |
| `PATCH` | `/modules/{name}/records/{record_id}` | Update a record |
| `DELETE` | `/modules/{name}/records/{record_id}` | Delete a record |

### Actions (tool modules)

| Method | Path | Description |
|---|---|---|
| `POST` | `/modules/{name}/action/{action_id}` | Invoke a module action. Body: `{ "params": { ... } }` |
| `POST` | `/modules/{name}/records/{record_id}/action/{action_id}` | Invoke a record-scoped action |

Action invocation returns `200` with the result when executed, or `202` when the
action's permission level requires approval.

### Module secrets

| Method | Path | Description |
|---|---|---|
| `GET` | `/modules/{name}/secrets-status` | Which declared secrets are configured |
| `PUT` | `/modules/{name}/secrets/{key}` | Set a secret. Body: `{ "value": "..." }` |
| `DELETE` | `/modules/{name}/secrets/{key}` | Delete a secret |

Secrets are stored encrypted (Fernet keyed from `data_encryption_key`) with a
`sentinel:v1:` envelope.

---

## WebSocket

Agent execution events stream over WebSocket. The WS surface is mounted under a
dedicated prefix:

| Path | Description |
|---|---|
| `WS /ws/instances/{instance_name}/sessions/{id}/stream` | Stream agent run events for a session |
| `WS /ws/instances/{instance_name}/sessions/{id}/terminals/{terminal_id}` | Attach to a runtime terminal |

Electron bridges these streams over the private backend socket. Renderer code
uses the desktop IPC channel; there are no session cookies or token query
parameters.

---

## Current limitations

- **Instance rename keeps the database name.** Renaming an instance does not
  move its UUID-based SQLite database directory.
- **Instance deletion is destructive.** Deleting an instance drops its database.
  Export a backup before deleting an instance you may need again.
- **Embedding model loading is shared.** The local model is reused across
  instances, while stored memories and searches remain instance-scoped.
