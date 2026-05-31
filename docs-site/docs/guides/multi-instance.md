---
sidebar_position: 4
title: Multi-Instance
---

# Multi-Instance

Sentinel runs as **one shared stack** (Postgres + backend + frontend) that hosts
**multiple logical instances**. There is a single manager database
(`sentinel_manager`) that tracks instance metadata, plus one auto-created app
database per instance for its sessions, memory, modules, triggers, and settings.

This is true for both deployment modes:

- **Docker Compose / server** — the stack is published on one port
  (`STACK_PORT`, default `4747`).
- **Desktop app** (macOS Apple Silicon) — the bundled Electron app runs the same
  backend as a managed child process against a bundled Postgres. The
  multi-instance model is identical; the desktop control center manages
  instances locally.

There is no longer a one-deployment-per-instance model: you do **not** spin up a
separate Compose project, port, or container per instance.

---

## Use cases

- Separate agents for different clients or projects
- Isolated dev vs. staging data
- Running different LLM providers/keys side by side (credentials are per-instance)
- Running one logical workspace per team member

---

## Setup

Create instances through the manager API, either from the CLI or from the
**Instance Picker** page in the UI.

```bash
bash ./sentinel-cli.sh
# Select: Instances
# Select: Create Instance
# Enter instance name: project-alpha
```

Instance names are normalized to lowercase letters, numbers, and dashes
(1–80 characters). Each instance gets:

- a manager registry row,
- its own Postgres database, named `sentinel_{safe-name}_{hash}` (the `hash` is
  derived from the original name, so it is stable),
- a runtime workspace root under the shared runtime workspace area.

Creating an instance bootstraps its database on demand: the database is created,
the instance schema migrations are run, and defaults are initialized. If
bootstrap fails, the instance row is rolled back and the database is dropped.

:::note Configure the LLM provider per instance
A new instance has **no LLM provider configured** until you set one. LLM
credentials (Anthropic, OpenAI, Gemini, etc.) are stored **encrypted in each
instance's settings database** — not in environment variables. Set them in the
UI under the instance's Settings, or via
`POST /instances/{instance_name}/settings/api-keys`. Until a provider is
configured, that instance's agent runtime cannot run.
:::

---

## Managing instances

```bash
./sentinel-cli.sh instances list
./sentinel-cli.sh instances create project-alpha "Project Alpha"
./sentinel-cli.sh instances rename project-alpha alpha
./sentinel-cli.sh instances delete alpha
```

The stack lifecycle remains shared across all instances:

```bash
./sentinel-cli.sh up
./sentinel-cli.sh status
./sentinel-cli.sh logs
./sentinel-cli.sh down
```

Equivalent admin-only API endpoints exist under `/api/v1/instances`
(`GET` list, `POST` create, `GET`/`PATCH`/`DELETE` by name, and
`POST /instances/{name}/rename`). See the
[CLI reference](./cli-reference.md) for command details.

**Rename caveat:** renaming an instance changes its name and URL routing but
**does not rename its underlying database**. The database name is derived from
the original name and stays fixed.

Deleting an instance drops its app database and removes its manager row.

---

## How requests are scoped

All instances share the same URL origin. The instance is selected in the route
path, not through separate ports or generated Compose files:

```
/api/v1/instances/{instance_name}/sessions
/api/v1/instances/{instance_name}/memory
/api/v1/instances/{instance_name}/triggers
...
```

The backend resolves the instance from the path and loads that instance's
`InstanceRuntimeContext` — its settings, tool registry/executor, agent runtime
support (only present once an LLM provider is configured), trigger scheduler,
and sub-agent orchestrator. The frontend auto-scopes API calls to the current
instance based on the `/instances/:instanceName/...` route.

Renaming, updating, or deleting an instance rebuilds (or removes) its runtime
context so credential and module changes take effect.

---

## Isolation model

| Isolated per logical instance | Shared by the stack |
|---|---|
| App database | Compose services / desktop processes |
| Sessions and history | Published HTTP port / origin |
| Memory tree | Manager (auth + instance metadata) database |
| Modules and permissions | Docker default network / host Docker daemon |
| LLM provider credentials | `DATA_ENCRYPTION_KEY` / `JWT_SECRET_KEY` (apply to all instances) |
| Runtime workspace root | Process-global embedding service |

Secrets stored in an instance's database (such as runtime SSH credentials) are
encrypted with the stack-wide `DATA_ENCRYPTION_KEY`. See
[Backup & restore](../reference/api.md) for how per-instance data is exported.

---

## Current limitations

Known constraints:

- **Embedding service is process-global**, not per-instance. Because the
  embedding API key is database-only, the embedding service may not initialize
  from per-instance settings as expected.
- **No automatic database-engine eviction.** Each active instance keeps its own
  Postgres engine/pool; there is no LRU/idle eviction yet, so creating many
  instances and leaving them idle can pressure the connection pool (TODO in
  code targets ~30 instances).
- **Sub-agents share the parent session's workspace and approval context** —
  isolation between a sub-agent and its parent is limited.
