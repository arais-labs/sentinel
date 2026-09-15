# Sentinel Backend

FastAPI runtime that powers Sentinel's agents, sessions, memory, triggers, runtimes,
modules, and operator controls.

## Source Of Truth

Start with the root setup and operations guide; it covers the desktop development workflow:

- [Root README](../../../README.md)

This component README is intentionally short and only covers backend-specific structure
and commands. For deeper architecture and API docs see the Docusaurus site under
`docs-site/`.

## Architecture (Multi-Instance)

The backend is **multi-instance**: a single process hosts **multiple logical instances**.

- **One manager database** (`app.sqlite`) holds global state: instance metadata,
  manager settings, audit logs, and the runtimes catalog.
- **One database per instance** (`instances/<UUID>/instance.sqlite`) holds that instance's sessions,
  messages, memories, triggers, modules, approvals, and `system_settings`. Instance
  databases are created on demand at startup and at instance-creation time, then migrated
  to the instance Alembic head.
- **`InstanceRuntimeContext`** is the per-instance runtime. Each instance gets its own
  `instance_settings`, session factory, tool registry, tool executor, trigger scheduler,
  sub-agent orchestrator, and `agent_runtime_support` (or `None` when no LLM provider is
  configured). The registry lives in
  `app/services/instance_runtime_context.py`.
- **Instance-scoped routes** carry the instance name in the path, e.g.
  `/api/v1/instances/{instance_name}/sessions`. Global routes (`/api/v1/instances`, `/api/v1/machines`) are not instance-scoped.

### LLM provider credentials are DB-only

LLM provider credentials (`anthropic_api_key`, `anthropic_oauth_token`, `openai_api_key`,
`gemini_api_key`, ...) are **not** read from environment variables. They are stored
encrypted in each instance's `system_settings` table and configured via the UI or
`POST /api/v1/instances/{instance_name}/settings/api-keys`. Legacy env vars are blocked at
the `Settings` level and trigger a startup warning. Infrastructure secrets
(`DATA_ENCRYPTION_KEY`, the private transport token) still come from the
environment supplied by the desktop service manager.

Configure providers in Settings or onboarding. For supported login options, see
[Provider connections](../../../docs-site/docs/guides/providers.md).

## Development and Tests

From the repository root, run `make setup` then `make dev`. Electron starts the
backend using embedded SQLite, with automatic reload when Python files change.
FastAPI runs through Uvicorn on a private Unix socket. Electron bridges requests
and streams to that socket. Sentinel has no user login or account system;
provider and runtime credentials are independent.

Run the backend tests directly with:

```bash
uv run --locked --directory apps/backend/sentinel pytest tests/ -q
```

Run `make check` from the root for all project checks.

## Database Schema

Two Alembic trees are configured (see `alembic.manager.ini` and `alembic.instance.ini`):

- **Manager** schema under `db/alembic/manager/`, initial revision `0001_manager_initial`.
- **Instance** schema under `db/alembic/instance/`, initial revision `0001_instance_initial`.

These are fresh-release baselines, not upgrade paths for previous development
revision histories. This change does not reset existing developer databases.

Backups use an explicit payload format (`schema_version: 3`), validated after
decryption. `created_by_version` is informational; restore is not gated by the
application version or a manually pinned Alembic head. Older backup formats are
not supported.

## Health Check

The health router is mounted without a prefix:

- `GET /health` — liveness, returns `{"status": "ok"}`.
- `GET /health/ready` — startup/readiness probe, returns `{"status": "ready"}`.

## Workspace Runtime Module

The built-in `runtime` module executes commands inside the attached Linux
workspace. Its grouped actions include `workspace`, `exec`, `terminal_list`,
window/pane management, `pane_input`, and `pane_read`. Commands run in tmux panes;
SSH connects Sentinel to remote machines running the workspace runtime.

See [Workspace Runtime](../../../docs-site/docs/guides/runtime-exec-security.md)
for storage, project paths, and execution behavior. Custom module Python actions
execute in the backend, not inside that workspace isolation boundary.

## Built-in Modules

`app/services/modules/builtins/__init__.py` registers the built-in definitions
through `get_builtins()`. Custom modules are defined in each instance's database.
`app/services/modules/tool_adapter.py` converts module definitions into tools
for the shared execution and approval pipeline.

Module actions use `allow`, `approval`, or `deny` permissions. Operator-facing
module HTTP actions return 202 when queued for approval; agent tool-level gates
wait for a decision inside tool execution.
