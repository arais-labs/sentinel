---
sidebar_position: 7
title: Approvals
---

# Approvals

The approval system lets operators gate sensitive agent actions behind explicit human review. The agent pauses, the operator decides, and execution resumes only with a green light.

Approvals are **per-instance**: every approval record lives in the database of the instance that produced it, and the HTTP endpoints are scoped under `/api/v1/instances/{instance_name}/approvals`. One operator can run many instances from a single deployment, and each instance keeps its own independent approval queue.

---

## Two ways an action gets gated

Sentinel gates work at two layers. Both end up writing rows to the same `tool_approvals` table, but they behave differently from the agent's point of view.

### 1. Tool-level approval gate (blocking)

System tools (for example `runtime_exec`) can require approval before they run. When this happens the tool call **does not return** — it creates a pending approval record and blocks, polling the database every **1.5 seconds** until an operator resolves it or the request times out.

- A pending callback fires immediately so the UI can surface the request.
- The waiter resolves to `approved`, `rejected`, `timed_out`, or `cancelled`.
- On approval the tool executes and its result is recorded back onto the approval row.

This path is implemented by the tool executor and its approval waiter. The agent never sees a raw HTTP status here; it simply waits on the tool call and then receives the tool result (success, rejection, or timeout) once the operator decides.

### 2. Module-action permission gate (202 / 403)

ARAIOS module actions are governed by the three-level permission map. The permission level set for an action determines what happens when the agent calls it:

| Permission level | Behavior |
|---|---|
| `allow` | Execute normally |
| `approval` | Create an approval record and return **HTTP 202** (pending) |
| `deny` | Return **HTTP 403** (blocked, no approval possible) |

See [Modules and permissions](./modules-and-permissions.md) for how these levels are seeded and overridden.

---

## The 202 vs 403 contract

This is the most important thing to understand about the module-action gate.

| Response | Meaning |
|---|---|
| **HTTP 202** | Action requires approval — created and pending, not an error |
| **HTTP 403** | Action is denied — blocked permanently, no approval possible |

When the permission for an action is `approval`, the API returns **202 Accepted** with the approval object. This is not a failure. The agent is expected to recognize the 202 as a pending approval and wait for resolution rather than treating it as an error.

A 403 means the permission is `deny`. There is no approval flow — the action will never proceed.

:::warning Common mistake
Treating a 202 response as an error and surfacing it as a failure to the user is wrong. A 202 means "waiting for human review", not "something went wrong". Always check for 202 before treating a module API response as an error.
:::

---

## How approvals are identified

Every approval row carries a `provider` field. **The provider is the tool or module name that requested it** (for example `runtime_exec`, or a module name) — it is not a fixed category like `tool` / `git` / `araios`. There is a single approval backend (`ToolApprovalProvider`) that serves every record; the provider value simply tells you which tool raised the request and is required when you resolve it.

Each record also stores:

- `approval_id` (UUID)
- `tool_name`, `action`, `description`
- `session_id` (the session the request came from, when applicable)
- `requested_by`, `status`, `expires_at`
- `payload_json` (request metadata) and `result_json` (recorded after the tool runs)
- `decision_by` / `decision_note` (filled in on resolution)

Approvals are tied to the session and request that created them — an approval raised in one session is not reusable to satisfy a pending call in another.

:::note
The `tool_approvals` table also has a `match_key` column, but the current approval pipeline does not populate or query it — there is no command-scoped match-key contract in the code today. Don't rely on `match_key` semantics.
:::

---

## Approval states

| State | Meaning |
|---|---|
| `pending` | Waiting for operator review |
| `approved` | Operator approved — execution proceeds |
| `rejected` | Operator denied — action will not execute |
| `timed_out` | No decision before `expires_at`; the request lapsed |
| `cancelled` | The waiting tool call was cancelled (for example the run was stopped) before a decision |

A tool-level approval times out automatically once its deadline passes: the row is flipped to `timed_out` and the blocking call returns.

---

## Approval flow step by step

For a module action gated at the `approval` level:

1. Agent attempts an action whose permission is `approval`
2. Sentinel returns **HTTP 202** with an approval object
3. Agent recognizes the 202, pauses that action, and informs the user
4. The pending approval shows up in the instance's approval queue
5. Operator reviews the action, the payload, and the context
6. Operator approves or rejects
7. On approval: the action proceeds
8. On rejection: the agent surfaces the rejection and continues without executing the action

For a tool-level gate the agent simply waits on the blocked tool call; once the operator resolves it (or it times out), the tool call returns with the corresponding outcome.

---

## Resolving an approval (API)

Resolution endpoints are admin-only and instance-scoped:

```
POST /api/v1/instances/{instance_name}/approvals/{provider}/{approval_id}/approve
POST /api/v1/instances/{instance_name}/approvals/{provider}/{approval_id}/reject
```

- `{provider}` must match the record's provider (the requesting tool/module name).
- Both accept an optional `note` in the body, recorded as `decision_note`.

Status codes:

| Code | Meaning |
|---|---|
| `404` | No approval matches that `provider` + `approval_id` |
| `409` | The approval is already resolved (not `pending`) |
| `400` | The provider is missing or the decision is unsupported |

---

## Who can resolve approvals

Only users with the `admin` role can resolve approvals; the `agent` role cannot. This is intentional — agents cannot self-approve their own requests. In the permission map, `approvals.resolve` is hard-set to `deny` for the agent.

---

## Filtering approvals

```
GET /api/v1/instances/{instance_name}/approvals?status=pending
GET /api/v1/instances/{instance_name}/approvals?status=approved
GET /api/v1/instances/{instance_name}/approvals?provider=runtime_exec
GET /api/v1/instances/{instance_name}/approvals?session_id=<uuid>
```

`limit` (1–500, default 100) and `offset` are also supported. Results are sorted newest-first.

---

## Approval for module registration

Creating or updating a module is itself an approval-gated action for agents: in the permission map `modules.create`, `modules.update`, and `modules.delete` default to `approval`, while `modules.list` is `allow`. So when an agent tries to register a new module, that request lands in the approval queue and an operator must approve it before the change takes effect.
