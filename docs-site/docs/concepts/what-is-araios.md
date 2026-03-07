---
sidebar_position: 2
title: What is araiOS?
---

# What is araiOS?

araiOS is the control plane that sits underneath Sentinel. It handles everything the agent is allowed to do: custom tools, persistent data stores, permissions, human approval gates, and agent coordination.

> **Sentinel is the agent. araiOS is the environment the agent operates within.**

Agents interact with araiOS exclusively through a REST API. Every action is auditable. There are no backdoors.

---

## Module system

araiOS is built around modules. A module is either a data store or a callable tool.

### Data modules
Persistent record stores with full CRUD. You define fields, types, and validation. Agents read, create, update, and delete records through scoped permission rules.

Examples: leads, clients, proposals, tasks, competitors.

### Tool modules
Callable actions backed by sandboxed Python. No stored records — just execution. You write the Python code; araiOS runs it in a controlled environment and passes secrets through a `secrets` dictionary in action execution context.

Examples: send a Slack message, call an external API, run a calculation, trigger a webhook.

---

## Permission model

Every agent action maps to one of three policies:

| Policy | What happens |
|---|---|
| `allow` | Agent executes immediately, no interruption |
| `approval` | Agent pauses, creates an approval request, waits for operator review |
| `deny` | Action is blocked, agent receives a 403 immediately |

The default policy for any unlisted action is `allow`. The `agent` role cannot resolve approvals — only `admin` can.

:::important
When an action hits `approval`, the araiOS API returns **HTTP 202**, not an error. The agent must handle 202 responses correctly — they mean "created and pending" not "failed". A 403 means "denied" and is permanent.
:::

---

## Approval flow

When a `202` is returned:

1. araiOS creates an `Approval` record with a `match_key`
2. The agent sees the 202, pauses the current action
3. The approval appears in the araiOS workspace under **Approvals**
4. The operator reviews the action, payload, and context
5. The operator approves or denies
6. The agent polls for resolution and resumes on approval, or surfaces the denial to the user

Approvals are matched to pending agent actions via `match_key`. If the key does not match — for example, if the request came from a different session — the approval will never resolve that action.

---

## Secrets management

API keys and credentials are stored at the module level. Agents never see raw secret values. Secrets are available to action code via a `secrets` dictionary and are never returned in API responses.

---

## Agent coordination

araiOS provides a coordination bus for multi-agent setups. Agents post messages, hand off tasks, and read coordination state through the same API. This is useful for orchestrating parallel sub-agents or signaling between agents in different sessions.

---

## Task system

Built-in task management shared between agents and operators:

- Status, priority, owner fields
- `workPackage` field for attaching plans, code, and artifacts
- Handoff workflows between agents and humans
- Supports filtering by client, status, owner, and date

---

## Discovering what is available

Always start with:

```
GET /api/agent
```

This returns the full guide for the current araiOS instance: available modules, registered actions, permission rules, and usage notes. Agents call this first when entering an unfamiliar instance.

---

## Key endpoints

```
GET  /api/agent                          # Full instance guide — call this first
GET  /api/modules                        # List all modules
POST /api/modules/:name/records          # Create a record in a data module
POST /api/modules/:name/action/:id       # Invoke a tool module action
GET  /api/tasks                          # List tasks
GET  /api/approvals?status=pending       # Check pending approvals
POST /api/approvals/:id/approve or /reject # Resolve an approval (admin only)
```
