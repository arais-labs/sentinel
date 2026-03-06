---
sidebar_position: 2
title: Creating Modules
---

# Creating araiOS Modules

Modules are the core extension point in araiOS. They define what data agents can store and what tools they can call.

---

## Data modules

A data module creates a persistent record store with full CRUD access.

Example — creating a `leads` module via the araiOS API:

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

Once registered, agents can use:
```
GET  /api/modules/leads/records
POST /api/modules/leads/records
PATCH /api/modules/leads/records/:id
DELETE /api/modules/leads/records/:id
```

---

## Tool modules

A tool module exposes callable actions backed by sandboxed Python.

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

---

## Permissions

New modules require approval before agents can use them. After creation, go to the araiOS workspace → Approvals to approve the module registration.

Then configure permission rules under **Permissions** to control which actions agents can call freely vs. which require approval.
