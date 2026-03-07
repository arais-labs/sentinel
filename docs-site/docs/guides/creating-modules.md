---
sidebar_position: 2
title: Creating Modules
---

# Creating araiOS Modules

Modules are how you extend what the agent can do and what data it can access. You define a module via the araiOS API, and the agent can interact with it immediately after it is approved.

---

## Two module types

| Type | What it does |
|---|---|
| **Data module** | Creates a persistent record store with full CRUD. Agents read, create, update, and delete records. |
| **Tool module** | Exposes callable Python actions. No stored records — just execution gated by permissions. |

---

## Creating a data module

```json
POST /api/modules
{
  "name": "leads",
  "type": "data",
  "label": "Leads",
  "fields": [
    { "name": "company", "type": "text", "required": true },
    { "name": "contact", "type": "text" },
    { "name": "tier", "type": "select", "options": ["1", "2", "3"] },
    { "name": "status", "type": "select", "options": ["new", "contacted", "qualified"] },
    { "name": "notes", "type": "text" }
  ]
}
```

Once registered and approved, agents can use:

```
GET    /api/modules/leads/records
POST   /api/modules/leads/records
PATCH  /api/modules/leads/records/:id
DELETE /api/modules/leads/records/:id
```

---

## Creating a tool module

```json
POST /api/modules
{
  "name": "slack",
  "type": "tool",
  "label": "Slack",
  "secrets": ["SLACK_BOT_TOKEN"],
  "actions": [
    {
      "id": "send_message",
      "label": "Send Message",
      "description": "Send a message to a Slack channel",
      "params": [
        { "name": "channel", "type": "text", "required": true },
        { "name": "message", "type": "text", "required": true }
      ],
      "code": "import os\nfrom slack_sdk import WebClient\nclient = WebClient(token=os.environ['SLACK_BOT_TOKEN'])\nclient.chat_postMessage(channel=params['channel'], text=params['message'])"
    }
  ]
}
```

The `code` field runs in a sandboxed Python environment. Secrets are injected as `os.environ` variables. Action params are available as the `params` dict.

---

## Approval requirement

New modules require operator approval before agents can use them. After `POST /api/modules`, the module appears in the araiOS workspace under **Approvals**. The operator approves the registration before the module is accessible.

This applies even to modules you created — the approval step is a deliberate gate.

---

## Setting permissions after creation

Once approved, configure permission rules for each action under **Permissions** in the araiOS workspace.

Default behavior: all actions are `allow` until you configure a rule.

Common setup for a sensitive tool module:

| Action | Policy |
|---|---|
| `invoke:send_message` | `approval` — require review before sending |
| `invoke:read_data` | `allow` — reading is low risk |
| `delete` | `deny` — block deletion entirely |

---

## Secrets

Secrets registered in a module are stored encrypted. They are never returned in API responses. Agents and operators cannot read them — they can only trigger actions that use them.

To update a secret value: use the araiOS workspace UI under **Modules → Secrets**.

---

## Discovering module endpoints

After creation, call `GET /api/agent` to see the module reflected in the full instance guide with its fields, actions, and current permission rules.
