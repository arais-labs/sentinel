---
sidebar_position: 7
title: Approvals
---

# Approvals

The approval system lets operators gate sensitive agent actions behind explicit human review. The agent pauses, the operator decides, and execution resumes only with a green light.

---

## Three approval providers

Sentinel uses three distinct approval providers, which are aggregated in the approval queue UI:

| Provider | What it covers |
|---|---|
| `tool` | Sentinel-native tool-level approval gate (blocking, in-process), including `runtime_exec` root mode |
| `git` | Git push and PR operations |
| `araios` | araiOS module actions (async, persisted to DB) |

Each provider is independent. You must pass the correct `provider` field when resolving an approval. Sending `provider=tool` to resolve a `git` approval will return a 404.

---

## The 202 vs 403 contract

This is the most important thing to understand about the approval system.

| Response | Meaning |
|---|---|
| **HTTP 202** | Action requires approval — created and pending, not an error |
| **HTTP 403** | Action is denied — blocked permanently, no approval possible |

When the araiOS permission for an action is set to `approval`, the API returns **202 Accepted** with the approval object. This is not a failure. The agent is expected to see the 202, recognize it as a pending approval, and wait for resolution.

A 403 means the permission is set to `deny`. There is no approval flow — the action will never proceed.

:::warning Common mistake
Treating a 202 response as an error and surfacing it as a failure to the user is wrong. A 202 means "waiting for human review", not "something went wrong". Always check for 202 before treating an araiOS response as an error.
:::

---

## Match key

When an approval is created, it is assigned a `match_key` derived from the tool call arguments and session context. This key is used to match the approval back to the pending tool call when it resolves.

If an approval was created in a different session or with different arguments, its match key will not match the pending tool call in the current session. The agent will never see the resolution.

This means approvals are **not portable between sessions**. An approval created in session A cannot resolve a pending call in session B.

### Runtime exec root key contract

`runtime_exec` uses an explicit scoped match key for root approvals:

- `runtime_exec:root:<normalized command>`

Examples:

- `runtime_exec:root:apt-get update`
- `runtime_exec:root:echo hello`

This exact key must match across:

1. approval creation
2. persisted `approval_hint` on tool calls
3. rehydration lookup on reconnect/refresh

---

## Approval flow step by step

1. Agent attempts an action with an `approval` permission rule
2. araiOS returns **HTTP 202** with an approval object
3. Agent recognizes the 202, pauses that action, and informs the user
4. Approval appears in the araiOS workspace under **Approvals**
5. Operator reviews the action, the payload, and the context
6. Operator approves or denies
7. On approval: the pending tool call is allowed to proceed
8. On denial: the agent surfaces the rejection to the user and continues without executing the action

---

## Approval states

| State | Meaning |
|---|---|
| `pending` | Waiting for operator review |
| `approved` | Operator approved — execution proceeds |
| `rejected` | Operator denied — action will not execute |

---

## Who can resolve approvals

Only users with the `admin` role can resolve approvals. The `agent` role cannot. This is intentional — agents cannot self-approve their own requests.

---

## Filtering approvals

```
GET /api/approvals?status=pending
GET /api/approvals?status=approved
GET /api/approvals?provider=araios
GET /api/approvals?session_id=<uuid>
```

---

## Approval for module registration

New araiOS modules require operator approval before agents can use them. After creating a module via the API, it appears in the approval queue. The operator approves the registration before the module becomes accessible.
