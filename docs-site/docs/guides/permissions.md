---
sidebar_position: 3
title: Permissions
---

# Permissions

araiOS permissions control what agents can do and when human approval is required.

---

## Permission rules

Each rule maps an action to a policy:

| Policy | Behavior |
|---|---|
| `allow` | Agent executes immediately, no interruption |
| `approval` | Agent pauses and surfaces a request for operator review |
| `deny` | Action is blocked entirely |

---

## Configuring rules

Rules are set in the araiOS workspace under **Permissions**.

Each rule specifies:
- **Action** — e.g. `create`, `delete`, `invoke:send_message`
- **Resource** — e.g. `leads`, `slack`, or `*` for all
- **Policy** — `allow`, `approval`, or `deny`

---

## Approval flow

When an agent hits an `approval` rule:

1. Agent pauses and posts an approval request to `/api/approvals`
2. Request appears in the araiOS workspace under **Approvals**
3. Operator reviews the action, payload, and context
4. Operator approves or rejects
5. Agent resumes (or surfaces the rejection to the user)

---

## Common patterns

| Scenario | Rule |
|---|---|
| Allow agents to read all data | `read` on `*` → `allow` |
| Require approval before deleting records | `delete` on `*` → `approval` |
| Block agents from modifying permissions | `*` on `permissions` → `deny` |
| Allow Slack messages freely | `invoke:send_message` on `slack` → `allow` |
