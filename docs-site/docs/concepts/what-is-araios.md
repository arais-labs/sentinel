---
sidebar_position: 2
title: What is araiOS?
---

# What is araiOS?

araiOS is the config-driven control plane that powers the structured layer underneath Sentinel. Where Sentinel is the agent runtime and operator interface, araiOS handles everything around it — custom tools, auth, permissions, approvals, and agent coordination.

> **Sentinel** is the agent. **araiOS** is the operating environment the agent works within.

---

## Module system

araiOS is built around modules. Operators define modules that agents interact with via a REST API. Two types:

### Data modules
Persistent record stores with full CRUD. Examples: leads, clients, proposals, tasks.

Agents can read, create, update, and delete records under scoped permissions. Data modules show up as structured stores in the araiOS workspace.

### Tool modules
Callable actions backed by sandboxed Python. No stored records — just execution. Examples: sending Slack messages, querying an external API, running a calculation.

---

## Permissions and approvals

Every agent action maps to a permission rule:

| Rule | Behavior |
|---|---|
| `allow` | Agent executes immediately |
| `approval` | Agent pauses, surfaces a request, waits for operator review |
| `deny` | Action is blocked |

When an action requires approval, the operator reviews and acts in the araiOS UI before execution continues. High-risk actions stay under human control without blocking normal work.

---

## Secrets management

API keys and credentials are stored at the module level in araiOS. Agents access them only through the controlled API surface — they never see raw credential values.

---

## Agent coordination

araiOS provides a coordination bus for multi-agent setups. Agents post messages, hand off tasks, and check coordination state — all through the same API.

---

## Task system

Built-in collaborative task management between agents and operators:

- Status, priority, owner fields
- Flexible `workPackage` for attaching plans and artifacts
- Handoff between agents and humans

---

## The API surface

Agents interact with araiOS exclusively through a documented REST API. Every action is auditable. No backdoor access.

Key endpoints:

```
GET  /api/agent                        # Discover available modules and endpoints
GET  /api/modules                      # List all modules
POST /api/modules/:name/records        # Create a record
POST /api/modules/:name/actions/:id    # Invoke a tool action
GET  /api/tasks                        # List tasks
GET  /api/approvals                    # Check pending approvals
```

---

## The araiOS workspace

araiOS ships with its own frontend at `/araios/`. Operators use it to:

- Register and configure modules
- Set permission rules per action
- Review and act on agent approval requests
- Inspect module records and action logs
