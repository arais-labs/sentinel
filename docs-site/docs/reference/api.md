---
sidebar_position: 1
title: Module API Reference
---

# Module API Reference

Agent module/control-plane interaction happens through this REST API.

:::tip Start here
Use `GET /api/instances/{instance_name}/modules` to discover the current module
catalog and `GET /api/instances/{instance_name}/permissions` to inspect action
policy.
:::

---

## Auth

Human users authenticate through Sentinel auth under `/api/v1/auth`.
Module/control-plane routes use the active Sentinel session cookie.

Module routes are scoped to a logical instance under
`/api/instances/{instance_name}`. Session, trigger, approval, and other app
routes are scoped under `/api/v1/instances/{instance_name}`.

---

## System

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/instances/{instance_name}/permissions` | List all permission actions and levels |
| `GET` | `/api/v1/instances/{instance_name}/approvals` | List approvals. Optional filter: `?status=pending|approved|rejected` |
| `POST` | `/api/v1/instances/{instance_name}/approvals` | Create approval record |
| `POST` | `/api/v1/instances/{instance_name}/approvals/{id}/approve` | Approve a pending approval |
| `POST` | `/api/v1/instances/{instance_name}/approvals/{id}/reject` | Reject a pending approval |

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
| `GET` | `/api/instances/{instance_name}/modules` | List modules |
| `GET` | `/api/instances/{instance_name}/modules/{name}` | Get module config |
| `POST` | `/api/instances/{instance_name}/modules` | Create module |
| `PATCH` | `/api/instances/{instance_name}/modules/{name}` | Update module |
| `DELETE` | `/api/instances/{instance_name}/modules/{name}` | Delete module |

---

## Records for data modules

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/instances/{instance_name}/modules/{name}/records` | List records |
| `GET` | `/api/instances/{instance_name}/modules/{name}/records/{id}` | Get one record |
| `POST` | `/api/instances/{instance_name}/modules/{name}/records` | Create record |
| `PATCH` | `/api/instances/{instance_name}/modules/{name}/records/{id}` | Update record |
| `DELETE` | `/api/instances/{instance_name}/modules/{name}/records/{id}` | Delete record |

---

## Actions for tool modules

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/instances/{instance_name}/modules/{name}/action/{action_id}` | Invoke tool action. Body: `{ "params": { ... } }` |

Returns `202` when approval is required, `200` with result when executed.

---

## Built in resources

| Resource | Endpoint prefix |
|---|---|
| Module registry | `/api/instances/{instance_name}/modules` |
| Module records | `/api/instances/{instance_name}/modules/{name}/records` |
| Module actions | `/api/instances/{instance_name}/modules/{name}/action/{action_id}` |

---

## Important distinction

Sentinel routes such as onboarding, memory, sessions, triggers, and websocket
chat live under `/api/v1/instances/{instance_name}`.
