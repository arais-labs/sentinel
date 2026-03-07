---
sidebar_position: 3
title: Permissions
---

# Permissions

Permissions control what agents can do with araiOS modules. Every action an agent attempts is checked against the permission rules before it executes.

---

## The three policies

| Policy | What happens |
|---|---|
| `allow` | Agent executes immediately — no interruption |
| `approval` | Agent pauses, creates an approval request, waits for operator decision |
| `deny` | Action blocked — agent receives HTTP 403 immediately |

**Default:** Any action without an explicit rule is `allow`.

---

## How rules work

Each rule maps an action pattern to a policy:

- **Action** — the operation being performed (`create`, `delete`, `invoke:send_message`, or `*` for wildcard)
- **Resource** — the module or resource being acted on (`leads`, `slack`, or `*` for all)
- **Policy** — `allow`, `approval`, or `deny`

Rules are evaluated in order. The first matching rule wins.

---

## Configuring rules

Rules are set in the araiOS workspace under **Permissions**.

### Examples

| Scenario | Action | Resource | Policy |
|---|---|---|---|
| Allow agents to read everything | `read` | `*` | `allow` |
| Require approval before deleting records | `delete` | `*` | `approval` |
| Block agents from modifying permissions | `*` | `permissions` | `deny` |
| Allow Slack messages without approval | `invoke:send_message` | `slack` | `allow` |
| Gate email sending behind approval | `invoke:send_email` | `email` | `approval` |

---

## Approval flow

When an agent hits an `approval` rule:

1. araiOS returns **HTTP 202** (not an error — see [Approvals](/concepts/approvals))
2. Agent recognizes the 202 and pauses that action
3. Approval request appears in the araiOS workspace under **Approvals**
4. Operator reviews the action, payload, and context
5. Operator approves or denies
6. Agent resumes on approval, or surfaces the rejection on denial

---

## Role-based behavior

| Role | Capabilities |
|---|---|
| `admin` | Can execute all actions, resolve approvals, manage permissions |
| `agent` | Executes actions subject to permission rules — cannot resolve approvals |

The `agent` role cannot self-approve its own requests. Only `admin` can resolve approval gates.

---

## Deny is immediate and permanent

A `deny` returns HTTP 403 instantly. There is no pending state, no approval flow, and no way for the agent to proceed. If you want the agent to eventually be able to do something pending review, use `approval`, not `deny`.

---

## Tips

- Start with `allow` for low-risk read operations, `approval` for writes and external calls, and `deny` for destructive or admin-level operations.
- Use `deny` on `permissions` and `modules` resources to prevent agents from modifying their own operating environment.
- Use `approval` on any action that sends data externally (email, Slack, webhooks) until you are confident in the agent's judgment.
