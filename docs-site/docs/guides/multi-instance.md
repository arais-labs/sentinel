---
sidebar_position: 4
title: Multi-Instance
---

# Multi-Instance

Sentinel runs one local Python backend for multiple instances. SQLite runs
inside that process; there is no database service.

`app.sqlite` holds the instance registry, shared machine configurations, and app
settings. Each instance stores its own data in
`instances/<stable-UUID>/instance.sqlite`, with an adjacent `attachments/` directory.
Databases use WAL mode, foreign keys, and short transactions.

An **instance** isolates chats, memories, settings, and accounts. A **session** is
a conversation inside that instance. A **machine** runs commands locally or over SSH. A **workspace** is a named directory on one machine, registered in an
instance and reusable across sessions. New chats start without a workspace; use
**Attach** in the session toolbar when files or tools are needed.

Shared project files stay in the chosen machine directory. Guest home, tools,
and session control state live in the workspace's private Linux filesystem.
Deleting a conversation preserves shared project files. See
[Workspaces](./workspaces.md) for persistence and execution boundaries.

---

## Use cases

- Separate agents for different clients or projects
- Isolated dev vs. staging data
- Running different LLM providers/keys side by side (credentials are per-instance)
- Keeping personal projects independent

---

## Setup

Create instances through the **Instance Picker** in the UI.

Instance names are normalized to lowercase letters, numbers, and dashes
(1–80 characters). Each instance gets:

- a manager registry row,
- its own SQLite file in a UUID-named directory,
- independent settings, credentials, and workspace registrations.

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

Use the instance picker to create and select instances. API endpoints under
`/api/v1/instances` also support listing, creation, renaming, and deletion.

**Rename caveat:** renaming an instance changes its name and URL routing but
**does not rename its underlying database**. The directory uses a stable UUID that stays fixed.

Deleting an instance closes its database connections, deletes its directory, and removes its registry row.

---

## How requests are scoped

All instances share the same URL origin. The instance is selected in the route
path, not through separate ports:

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
| App database | Desktop processes |
| Sessions and history | Private backend socket |
| Memory tree | Manager (instance metadata) database |
| Modules and permissions | Local service lifecycle |
| LLM provider credentials | `DATA_ENCRYPTION_KEY` (apply to all instances) |
| Workspaces and session attachments | Machines and their connection credentials |

Secrets stored in an instance's database (such as provider credentials) are
encrypted with the stack-wide `DATA_ENCRYPTION_KEY`. See
[Backup & restore](../reference/api.md) for how per-instance data is exported.

---

## Shared services and workspace boundaries

The local embedding model is loaded once per backend process to avoid duplicate
model memory. Embedding storage and retrieval still use each instance's database;
there is no external embedding API key to configure.

Sub-agents share their parent's attached workspace. Files and processes in that
workspace are not isolated per conversation. Session action grants are scoped to
the conversation and are not inherited by forks or child sessions.
