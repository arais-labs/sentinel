---
sidebar_position: 2
title: Modules and Permissions
---

# Modules and Permissions

A **module** is a capability available to the operator and agent: records,
actions, a page, or a combination of these. Permissions determine which actions
can run and which require your review.

## Built-in and custom modules

| Kind | Ownership | Typical use |
|---|---|---|
| Built-in | Shipped with Sentinel | Runtime tools, memory, forms, and agent coordination |
| Custom | Defined inside an instance | Project records, API integrations, and reference pages |

Custom modules can combine these features:

- **Records:** fields and validation for structured data, such as tasks or contacts.
- **Actions:** Python code that performs work using the action execution context.
- **Pages:** Markdown content that operators and agents can read and update.
- **Secrets:** credentials supplied to action code without returning their values
  in module API responses.

There is no mutually exclusive module `type` field. See
[Creating Modules](../guides/creating-modules.md) for the schema and examples.

## Permission model

| Policy | Module action behavior |
|---|---|
| `allow` | Execute immediately |
| `approval` | Create a pending approval and return HTTP 202 |
| `deny` | Block the action with HTTP 403 |

Built-in defaults and configured overrides determine the effective policy.
Operators resolve approvals; agents cannot approve their own requests.

:::info Pending is not failed
HTTP 202 means the action needs a decision. Do not retry it as a failed request.
HTTP 403 means the current policy blocks it. Tool-level approval gates wait
inside tool execution instead of exposing an HTTP response to the agent.
:::

## Review an action

1. The agent attempts an action that needs approval.
2. Sentinel records the request with its tool or module identifier and session.
3. Review the action and payload in its approval card.
4. Choose **Deny**, **Approve once**, or **Allow for this session**.

Session approval grants apply to that action in that conversation, not every
module action or other conversations. See [Approvals](./approvals.md) for grant
scope, revocation, and the full execution contract.

## Discover modules through the API

All routes are scoped to an instance:

```http
GET /api/v1/instances/{instance_name}/modules
GET /api/v1/instances/{instance_name}/permissions
GET /api/v1/instances/{instance_name}/modules/{name}/records
POST /api/v1/instances/{instance_name}/modules/{name}/records
POST /api/v1/instances/{instance_name}/modules/{name}/action/{action_id}
```

To resolve a request, use both the `provider` and `id` from its approval record:

```http
GET /api/v1/instances/{instance_name}/approvals?status=pending
POST /api/v1/instances/{instance_name}/approvals/{provider}/{approval_id}/approve
POST /api/v1/instances/{instance_name}/approvals/{provider}/{approval_id}/reject
```

For request bodies and response codes, use the [API reference](../reference/api.md).
