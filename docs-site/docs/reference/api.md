---
sidebar_position: 1
title: API Reference
---

# API Reference

Sentinel exposes a single FastAPI HTTP surface for the whole deployment. One
deployment hosts **multiple logical instances**, so almost every feature route is
scoped by an instance name in the path:

```
/api/v1/instances/{instance_name}/...
```

A handful of routes are **manager-scoped** (not tied to a single instance):
authentication, instance CRUD, runtime targets, and manager admin/audit.

:::tip Start here
- `GET /api/v1/version` — identify the running build.
- `GET /api/v1/instances` — list the instances on this deployment.
- `GET /api/v1/instances/{instance_name}/modules` — discover the module catalog.
- `GET /api/v1/instances/{instance_name}/permissions` — inspect action policy.
:::

---

## Auth and scoping

Human users authenticate under `/api/v1/auth`. On success the server sets two
httponly cookies — `sentinel_access_token` and `sentinel_refresh_token` — which
carry the JWT used for all subsequent requests. All routes except `/api/v1/auth`,
`/health*`, and `/api/v1/version` require a valid session.

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

### Authentication — `/api/v1/auth`

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Exchange username/password for a token pair (sets cookies) |
| `GET` | `/api/v1/auth/status` | Whether auth is configured |
| `POST` | `/api/v1/auth/bootstrap` | First-run credential bootstrap — **desktop mode only** |
| `POST` | `/api/v1/auth/refresh` | Rotate the access token from the refresh token |
| `POST` | `/api/v1/auth/change-password` | Change password — **desktop mode only** (returns `404` in server/compose mode) |
| `GET` | `/api/v1/auth/me` | Current identity |
| `DELETE` | `/api/v1/auth/session` | Log out and revoke the session |

:::warning Server vs desktop auth
In **server/compose mode** the root `.env` is the source of truth: the backend
reads `SENTINEL_AUTH_USERNAME` / `SENTINEL_AUTH_PASSWORD` on every startup and
syncs them to the manager database, so `/auth/bootstrap` and
`/auth/change-password` are disabled (`404`). Rotate credentials via `.env` +
restart. In **desktop mode** (`APP_ENV=desktop`) the manager database is the
source of truth after first launch, and both endpoints are enabled.
:::

### Instances — `/api/v1/instances`

All instance management endpoints require the `admin` role.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/instances` | List instances |
| `POST` | `/api/v1/instances` | Create an instance (creates its database, runs instance migrations, seeds defaults) |
| `GET` | `/api/v1/instances/{name}` | Get one instance |
| `PATCH` | `/api/v1/instances/{name}` | Update an instance (rebuilds its runtime context) |
| `POST` | `/api/v1/instances/{name}/rename` | Rename an instance (rebuilds context; the underlying database name is **not** renamed) |
| `DELETE` | `/api/v1/instances/{name}` | Delete an instance (`204`) |

:::note
Deleting an instance does not automatically drop its Postgres database — the
`database_name` can be left orphaned and must be dropped manually if you want to
reclaim it.
:::

### Runtimes — `/api/v1/runtimes`

SSH-managed runtime targets registered in the manager database and optionally
linked to an instance.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/runtimes` | List runtime targets |
| `POST` | `/api/v1/runtimes` | Create a runtime target |
| `GET` | `/api/v1/runtimes/capabilities` | Supported runtime capabilities |
| `GET` | `/api/v1/runtimes/{runtime_id}` | Get one runtime target |
| `PATCH` | `/api/v1/runtimes/{runtime_id}` | Update a runtime target |
| `DELETE` | `/api/v1/runtimes/{runtime_id}` | Delete a runtime target (`204`) |
| `POST` | `/api/v1/runtimes/{runtime_id}/{action}` | Run an action (`start` / `stop` / `test` …) |
| `POST` | `/api/v1/runtimes/test` | Test a connection without persisting |
| `GET` | `/api/v1/runtimes/jobs/{job_id}` | Poll an async runtime job |
| `PATCH` | `/api/v1/instances/{name}/runtime` | Link an instance to a runtime target |

### Manager admin — `/api/v1/admin`

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/admin/audit` | Manager-level audit log |

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
| `GET` | `/sessions/default` | Get the main session |
| `POST` | `/sessions/default/reset` | Reset the main session |
| `GET` | `/sessions/{id}` | Get a session |
| `PATCH` | `/sessions/{id}` | Update a session |
| `POST` | `/sessions/{id}/main` | Mark a session as the main session |
| `DELETE` | `/sessions/{id}` | Delete a session (cascades messages, sub-agent tasks, summaries) |
| `POST` | `/sessions/{id}/chat` | Run the agent for one turn |
| `POST` | `/sessions/{id}/stop` | Cancel the active agent run |
| `GET` | `/sessions/{id}/messages` | List messages |
| `POST` | `/sessions/{id}/messages` | Post a user message |
| `POST` | `/sessions/{id}/messages/{message_id}/retry` | Retry from a message |
| `POST` | `/sessions/{id}/read` | Mark read |
| `GET` | `/sessions/{id}/context-usage` | Token budget / usage metrics |

Runtime workbench (file explorer, git diff, port forwarding) hangs off the same
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
| `POST` | `/settings/logging/levels` | Override logging levels (desktop mode) |
| `DELETE` | `/settings/logging/levels` | Remove a logging override |
| `POST` | `/settings/logging/reset` | Reset logging levels |

### Backup / restore

| Method | Path | Description |
|---|---|---|
| `GET` | `/backup/items` | Selectable item categories (`sessions`, `memories`, `modules`, `triggers`) |
| `POST` | `/backup/export` | Export an encrypted, passphrase-protected backup |
| `POST` | `/backup/inspect` | Inspect a backup file (version, items) |
| `POST` | `/backup/import` | Restore (version-gated; rebuilds the runtime context if `modules` are imported) |

Restores are version-gated (`MIN_RESTORABLE_VERSION = 0.1.2`, plus a forward
guard on the major version) and schema-verified against the pinned instance
Alembic head. Backups are encrypted with AES-GCM using a scrypt-derived key
from the passphrase, which is required for both export and import.

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
provider (`tool`, `git`, `araios`).

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

Modules are the agent's tools and data surfaces. The catalog includes static
**system modules** (e.g. `http_request`, `browser`, `runtime`, `git_tool`,
`memory`, `sub_agents`, `triggers`, `telegram`, `module_manager`, `tasks`,
`documents`, `coordination`) and dynamic **user-defined modules**. The module
registry is per-instance.

### Module registry

| Method | Path | Description |
|---|---|---|
| `GET` | `/modules` | List modules (system + custom) |
| `POST` | `/modules` | Create a dynamic module (`201`) |
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

Authentication uses the session cookie (or a token query parameter).

---

## Current limitations

- **Password change / bootstrap are server-mode disabled.** In server/compose
  mode `/auth/bootstrap` and `/auth/change-password` return `404`; rotate
  credentials via `.env` + restart.
- **Instance rename keeps the database name.** Renaming an instance does not
  rename its underlying Postgres database.
- **Instance deletion does not drop the database.** Orphaned `database_name`
  rows must be cleaned up manually.
- **Embeddings are process-global, not per-instance.** The embedding service is
  initialized once at boot and is not scoped to an instance's settings.
