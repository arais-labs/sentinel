# Sentinel Backend

FastAPI runtime that powers Sentinel's agents, sessions, memory, triggers, runtimes,
modules, and operator controls.

## Source Of Truth

Start with the root setup and operations guide; it covers stack-level config, the CLI,
and the desktop app:

- [Root README](../../../README.md)

This component README is intentionally short and only covers backend-specific structure
and commands. For deeper architecture and API docs see the Docusaurus site under
`docs-site/`.

## Architecture (Multi-Instance)

The backend is **multi-tenant**: a single process hosts **multiple logical instances**.

- **One manager database** (`sentinel_manager`) holds global state: instance metadata,
  revoked tokens, manager settings, audit logs, and the runtimes catalog.
- **One database per instance** (`sentinel_{name}_{hash}`) holds that instance's sessions,
  messages, memories, triggers, modules, approvals, and `system_settings`. Instance
  databases are created on demand at startup and at instance-creation time, then migrated
  to the instance Alembic head.
- **`InstanceRuntimeContext`** is the per-instance runtime. Each instance gets its own
  `instance_settings`, session factory, tool registry, tool executor, trigger scheduler,
  sub-agent orchestrator, and `agent_runtime_support` (or `None` when no LLM provider is
  configured). The registry lives in
  `app/services/instance_runtime_context.py`.
- **Instance-scoped routes** carry the instance name in the path, e.g.
  `/api/v1/instances/{instance_name}/sessions`. Global routes (`/api/v1/auth`,
  `/api/v1/instances`, `/api/v1/runtimes`) are not instance-scoped.

### LLM provider credentials are DB-only

LLM provider credentials (`anthropic_api_key`, `anthropic_oauth_token`, `openai_api_key`,
`gemini_api_key`, ...) are **not** read from environment variables. They are stored
encrypted in each instance's `system_settings` table and configured via the UI or
`POST /api/v1/instances/{instance_name}/settings/api-keys`. Legacy env vars are blocked at
the `Settings` level and trigger a startup warning. Infrastructure secrets
(`DATA_ENCRYPTION_KEY`, `JWT_SECRET_KEY`, Postgres credentials) still come from the
environment / root `.env`.

## Run (Via Stack Compose)

From the repo root (brings up Postgres + this backend with reload):

```bash
docker compose -f docker-compose.dev.yml up --build postgres sentinel-backend
```

The dev backend runs Uvicorn on container port `8000`. `DATA_ENCRYPTION_KEY` and
`JWT_SECRET_KEY` are required and apply to every instance.

## Tests

This package uses `uv`. From the repo root, run the suite inside the backend container:

```bash
docker compose -f docker-compose.dev.yml exec -T sentinel-backend python -m pytest -q
```

To run locally against the backend venv (created by `uv`, at `apps/backend/sentinel/.venv`):

```bash
uv run --project apps/backend/sentinel python -m pytest -q
```

## Database Migrations

Two Alembic trees are configured (see `alembic.manager.ini` and `alembic.instance.ini`):

- **Manager** schema under `db/alembic/manager/` (current head: the latest `000x_*` revision).
- **Instance** schema under `db/alembic/instance/` (baseline head: `0000_instance_v1`).

Backup/restore is pinned to `VERIFIED_INSTANCE_ALEMBIC_HEAD=0000_instance_v1`; when an
instance migration moves the head it must be re-affirmed in
`app/services/backup/engine.py` or restores will fail the schema check.

## Health Check

The health router is mounted without a prefix:

- `GET /health` — liveness, returns `{"status": "ok"}`.
- `GET /health/ready` — startup/readiness probe, returns `{"status": "ready"}`.

## Runtime Tool Module

Shell execution runs through the SSH/tmux managed runtime (`runtime` system module). Its
actions are:

- `user` — run a shell command in the session's tmux-backed sandbox workspace. Supports
  `cwd`, `terminal_id`, `timeout_seconds` (default 300), `background`, and `env`.
- `terminal_list` — list active tmux-backed terminals for the session.
- `terminal_read` — read recent ANSI-stripped output from one or more terminals.
- `terminal_close` — close one or more terminals (terminal `0` is the prioritized main
  terminal and is recreated on demand).

macOS runtimes apply Seatbelt sandboxing; Linux uses bubblewrap. There is no separate
`root` action or job-control (`jobs`/`job_status`/...) action in the current module.

## System Tool Modules

Fourteen native modules are registered (see
`app/services/araios/system_modules/__init__.py`): `http_request`, `browser`, `runtime`,
`port_forward`, `git_tool`, `str_replace_editor`, `memory`, `sub_agents`, `telegram`,
`triggers`, `module_manager`, `tasks`, `documents`, `coordination`. Instances can also
define dynamic (user-defined) modules. Each module action carries one of three permission
levels: `allow` (run), `approval` (create an approval record, return HTTP 202), or `deny`
(return HTTP 403).
