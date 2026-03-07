---
sidebar_position: 2
title: Creating Modules
---

# Creating araiOS modules

This page is code accurate to `apps/backend/araios/app/routers/modules.py` and executor behavior.

---

## Module types

| Type | Behavior |
|---|---|
| `data` | Record CRUD on `/api/modules/{name}/records` plus optional custom actions |
| `tool` | No records, callable actions executed by sandbox executor |

---

## Correct payload shape for module creation

Endpoint:

```http
POST /api/modules
```

### Data module example

```json
{
  "name": "leads",
  "label": "Leads",
  "type": "data",
  "description": "Lead pipeline",
  "fields": [
    { "key": "company", "label": "Company", "type": "text", "required": true },
    { "key": "status", "label": "Status", "type": "select", "options": ["new", "contacted", "qualified"] }
  ],
  "actions": [],
  "secrets": []
}
```

### Tool module example

```json
{
  "name": "slack",
  "label": "Slack",
  "type": "tool",
  "description": "Slack actions",
  "secrets": [
    { "key": "SLACK_BOT_TOKEN", "required": true }
  ],
  "actions": [
    {
      "id": "send_message",
      "label": "Send message",
      "description": "Send text to channel",
      "params": [
        { "key": "channel", "type": "text", "required": true },
        { "key": "message", "type": "text", "required": true }
      ],
      "code": "token = secrets.get('SLACK_BOT_TOKEN')\nif not token:\n    result = {'ok': False, 'error': 'missing token'}\nelse:\n    # call API here\n    result = {'ok': True}"
    }
  ]
}
```

Important field conventions:

- use `key` for fields and params
- action identifier is `id`
- `actions` and `secrets` are arrays of objects

---

## Action invocation paths

### Tool module action

```http
POST /api/modules/{name}/action/{action_id}
```

Body:

```json
{ "params": { "channel": "#ops", "message": "hello" } }
```

### Data module custom action on a record

```http
POST /api/modules/{name}/records/{id}/action/{action_id}
```

---

## Permission and approval behavior

Before execution, router checks action permission.

- `allow` -> execute
- `deny` -> 403
- `approval` -> creates approval record and returns 202 with approval object

Default if missing permission row is allow.

---

## Execution context in action code

Executor injects variables into code context:

- `params` dict
- `secrets` dict
- `record` dict for record scoped actions
- `http` async client
- `result` optional return payload

Secrets are passed through `secrets` dict, not via `os.environ`.

---

## Executor sandbox reality

The sandbox blocks dangerous modules like subprocess, pty, multiprocessing, ctypes, signal.

It is permissive for many normal builtins and libraries.

Do not claim hard isolation. Treat action code as privileged and review it accordingly.

---

## Updating a module

Endpoint:

```http
PATCH /api/modules/{name}
```

Editable fields include:

- label
- icon
- type
- fields
- list_config
- actions
- secrets
- description
- order

When actions change, permission rows for stale actions are cleaned and new defaults are seeded.

---

## Quick validation checklist

After creating module:

1. `GET /api/modules/{name}` returns expected schema
2. permission rows exist for intended actions
3. test action returns expected result on allow
4. switch one action to approval and verify 202 flow
5. confirm secrets are configured before production use
