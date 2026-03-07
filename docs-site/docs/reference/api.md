---
sidebar_position: 1
title: araiOS API Reference
---

# araiOS API Reference

All agent-to-araiOS interaction happens through this REST API. Every call is auditable.

:::tip Start here
Always begin with `GET /api/agent`. It returns the full guide for the current instance: modules, endpoints, permission rules, and usage context. Agents call this first when entering an unfamiliar instance.
:::

---

## System

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/agent` | Full instance guide — modules, endpoints, permissions, context |
| `GET` | `/api/manifest` | Machine-readable tool manifest |
| `GET` | `/api/permissions` | List all permission rules |
| `GET` | `/api/approvals` | List approval requests. Filter: `?status=pending\|approved\|rejected`, `?provider=tool\|git\|araios`, `?session_id=<uuid>` |
| `POST` | `/api/approvals` | Create a manual approval request |
| `POST` | `/api/approvals/:id/resolve` | Resolve an approval (`admin` role required). Body: `{ "decision": "approved\|rejected", "note": "optional" }` |

### Approval response codes

| Code | Meaning |
|---|---|
| `202` | Action requires approval — created and pending. Not an error. |
| `403` | Action is denied by a `deny` rule. Permanent. |
| `404` | Approval ID not found, or wrong `provider` specified for resolution. |

---

## Modules

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/modules` | List all registered modules |
| `GET` | `/api/modules/:name` | Get module config: fields, actions, secrets schema, permission rules |
| `POST` | `/api/modules` | Register a new module (requires approval) |
| `PATCH` | `/api/modules/:name` | Update module config |
| `DELETE` | `/api/modules/:name` | Delete a module |

---

## Records (data modules)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/modules/:name/records` | List records. Filter: `?filter_field=<field>&filter_value=<value>` |
| `POST` | `/api/modules/:name/records` | Create a record |
| `PATCH` | `/api/modules/:name/records/:id` | Update a record (partial update) |
| `DELETE` | `/api/modules/:name/records/:id` | Delete a record |

---

## Actions (tool modules)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/modules/:name/actions/:action_id` | Invoke a tool action. Body: `{ "params": { ... } }` |

Returns `202` if the action requires operator approval. Returns the action result directly on `200`.

---

## Tasks

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/tasks` | List tasks. Filter: `?client=&status=&owner=&priority=` |
| `POST` | `/api/tasks` | Create a task |
| `PATCH` | `/api/tasks/:id` | Update task fields (partial update) |
| `DELETE` | `/api/tasks/:id` | Delete a task |

### Task fields

| Field | Type | Description |
|---|---|---|
| `title` | string | Task title |
| `status` | string | `open`, `in_progress`, `blocked`, `done` |
| `priority` | string | `low`, `medium`, `high` |
| `owner` | string | Assigned agent or human |
| `client` | string | Associated client or project |
| `workPackage` | object | Arbitrary structured payload — plans, code, artifacts |
| `handoffTo` | string | Who to hand off to next |

---

## Coordination

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/coordination` | List coordination messages |
| `POST` | `/api/coordination` | Post a coordination message |

Used for multi-agent communication and state signaling.

---

## Documents

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/documents` | List documents |
| `POST` | `/api/documents` | Create a document |
| `PATCH` | `/api/documents/:id` | Update a document |
| `DELETE` | `/api/documents/:id` | Delete a document |

---

## Onboarding

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/onboarding/status` | Check if onboarding has been completed |
| `POST` | `/api/onboarding/complete` | Mark onboarding as complete |

---

## Auth notes

All requests require a valid Bearer token obtained via the `/token` endpoint using an API key. Tokens expire — the client is responsible for refreshing before expiry using the refresh token.

The `agent` role can call all module and data endpoints subject to permission rules. It cannot resolve approvals or modify permissions.

The `admin` role can do everything.
