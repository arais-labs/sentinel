---
sidebar_position: 1
title: araiOS API Reference
---

# araiOS API Reference

All agent-araiOS interaction happens through this REST API. Start with `GET /api/agent` to discover current endpoints and modules.

---

## System endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/agent` | Full guide — call this first to understand available modules and endpoints |
| `GET` | `/api/manifest` | Machine-readable tool manifest |
| `GET` | `/api/permissions` | List all permission rules |
| `GET` | `/api/approvals` | List approval requests. Filter: `?status=pending\|approved\|rejected` |
| `POST` | `/api/approvals` | Create a manual approval request |

---

## Modules

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/modules` | List all registered modules |
| `GET` | `/api/modules/:name` | Get module config including fields, actions, secrets schema |
| `POST` | `/api/modules` | Register a new module (subject to approval) |
| `PATCH` | `/api/modules/:name` | Update module config |
| `DELETE` | `/api/modules/:name` | Delete a module |

---

## Records (data modules)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/modules/:name/records` | List records. Filter: `?filter_field=&filter_value=` |
| `POST` | `/api/modules/:name/records` | Create a record |
| `PATCH` | `/api/modules/:name/records/:id` | Update a record |
| `DELETE` | `/api/modules/:name/records/:id` | Delete a record |

---

## Actions (tool modules)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/modules/:name/actions/:action_id` | Invoke a tool action |

---

## Tasks

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/tasks` | List tasks. Filter: `?client=&status=&owner=` |
| `POST` | `/api/tasks` | Create a task |
| `PATCH` | `/api/tasks/:id` | Update task fields |
| `DELETE` | `/api/tasks/:id` | Delete a task |

---

## Coordination

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/coordination` | List coordination messages |
| `POST` | `/api/coordination` | Post a coordination message |
