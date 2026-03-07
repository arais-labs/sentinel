---
sidebar_position: 1
title: araiOS API Reference
---

# araiOS API Reference

All agent to araiOS interaction happens through this REST API.

:::tip Start here
Always begin with `GET /api/agent`. It returns the full guide for the current instance: modules, endpoints, permission rules, and usage context.
:::

---

## Auth and token exchange

araiOS platform auth routes live under `/platform/auth`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/platform/auth/token` | Exchange API key for access and refresh tokens |
| `POST` | `/platform/auth/refresh` | Refresh access token |
| `POST` | `/platform/auth/logout` | Revoke current token set |
| `GET` | `/platform/auth/me` | Current identity |

Most `/api/*` routes require a valid bearer access token.

---

## System

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/agent` | Full instance guide with module capabilities |
| `GET` | `/api/manifest` | Machine readable manifest |
| `GET` | `/api/permissions` | List all permission actions and levels |
| `GET` | `/api/approvals` | List approvals. Optional filter: `?status=pending|approved|rejected` |
| `POST` | `/api/approvals` | Create approval record |
| `POST` | `/api/approvals/{id}/approve` | Approve a pending approval |
| `POST` | `/api/approvals/{id}/reject` | Reject a pending approval |

### Approval response codes

| Code | Meaning |
|---|---|
| `202` | Action requires approval and was queued as pending |
| `403` | Action denied by policy |
| `404` | Approval id not found |

---

## Modules

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/modules` | List modules |
| `GET` | `/api/modules/{name}` | Get module config |
| `POST` | `/api/modules` | Create module |
| `PATCH` | `/api/modules/{name}` | Update module |
| `DELETE` | `/api/modules/{name}` | Delete module |

---

## Records for data modules

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/modules/{name}/records` | List records |
| `GET` | `/api/modules/{name}/records/{id}` | Get one record |
| `POST` | `/api/modules/{name}/records` | Create record |
| `PATCH` | `/api/modules/{name}/records/{id}` | Update record |
| `DELETE` | `/api/modules/{name}/records/{id}` | Delete record |

---

## Actions for tool modules

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/modules/{name}/action/{action_id}` | Invoke tool action. Body: `{ "params": { ... } }` |

Returns `202` when approval is required, `200` with result when executed.

---

## Built in resources

| Resource | Endpoint prefix |
|---|---|
| Tasks | `/api/tasks` |
| Coordination messages | `/api/coordination` |
| Documents | `/api/documents` |
| Settings | `/api/settings` |

---

## Important distinction

Sentinel routes such as onboarding, memory, sessions, triggers, and websocket chat are in Sentinel backend APIs, not araiOS APIs.
