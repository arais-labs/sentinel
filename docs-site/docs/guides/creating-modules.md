---
sidebar_position: 2
title: Creating Modules
---

# Creating Modules

Modules are the unit of custom capability in Sentinel. A module bundles a data
schema, callable actions (Python code), secrets, and optional page content, and
exposes them both to the operator UI and to the agent as tools.

This page is code-accurate to the module schema (`app/schemas/modules.py`), the
module router (`app/routers/araios/modules.py`), the dynamic-module tool builder
(`app/services/araios/dynamic_modules.py`), and the action executor
(`app/services/araios/executor.py`).

:::info Modules are per-instance
Sentinel runs **one deployment hosting multiple logical instances**. Modules
are stored in each instance's own database and are scoped under
`/api/v1/instances/{instance_name}/modules`. A module created in one instance is
not visible in another. Creating, updating, importing, or deleting a module
rebuilds that instance's runtime context so the agent's tool registry picks up
the change immediately.
:::

---

## What a module is made of

A module has no `type` field. Its "shape" is implied by which parts you fill in:

| You provide | Resulting behavior |
|---|---|
| `fields` (+ records via the records API) | A data module: record CRUD on `/modules/{name}/records`, plus any custom record/standalone actions |
| `actions` only | A tool module: no records, just callable actions executed by the sandbox |
| `page_title` / `page_content` | A page module: markdown content surfaced in the UI and via the `get_page` / `edit_page` agent commands |

These are not mutually exclusive — a single module can carry fields, actions,
and a page at once. The UI renders the relevant panes based on what is present.

---

## Creating a module

Endpoint (admin-scoped, per instance):

```http
POST /api/v1/instances/{instance_name}/modules
```

The request body is validated by `ModuleDefinitionPayload` with
`extra="forbid"` — **unknown top-level keys are rejected with HTTP 400.** There
is no `type` key; do not send one.

### Top-level fields

| Field | Type | Notes |
|---|---|---|
| `name` | string, required | Normalized to lowercase on save |
| `label` | string, required | Human-readable name |
| `description` | string | Defaults to `""` |
| `icon` | string | Defaults to `"box"` |
| `fields` | array of field objects | Default `[]` |
| `fields_config` | object | Display hints (`titleField`, `subtitleField`, `badgeField`, `filterField`, `metaField`) |
| `actions` | array of action objects | Default `[]` |
| `secrets` | array of secret objects | Default `[]` |
| `page_title` | string \| null | Optional page module title |
| `page_content` | string \| null | Optional page module markdown |
| `order` | integer | Sort order, default `100` |
| `permissions` | object | Optional per-command permission overrides (see [Permissions](#permissions)) |

### Data module example

```json
{
  "name": "leads",
  "label": "Leads",
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
  "description": "Slack actions",
  "secrets": [
    { "key": "SLACK_BOT_TOKEN", "label": "Slack Bot Token", "required": true }
  ],
  "actions": [
    {
      "id": "send_message",
      "label": "Send message",
      "description": "Send text to a channel",
      "params": [
        { "key": "channel", "label": "Channel", "type": "text", "required": true },
        { "key": "message", "label": "Message", "type": "text", "required": true }
      ],
      "code": "token = secrets.get('SLACK_BOT_TOKEN')\nif not token:\n    result = {'ok': False, 'error': 'missing token'}\nelse:\n    # call the Slack API via the injected http client\n    result = {'ok': True}"
    }
  ]
}
```

### Field, param, and secret conventions

- **Fields and params use `key`** (not `name`) plus a required `label`.
  Field `type` is one of: `text`, `textarea`, `email`, `url`, `number`, `date`,
  `select`, `badge`, `tags`, `readonly`.
- **Action identifier is `id`** (lowercased on save). Custom action ids may not
  collide with the reserved record commands: `list_records`, `get_record`,
  `create_records`, `update_records`, `delete_records`, `get_page`, `edit_page`.
- **Secrets must be objects with both `key` and `label`** (each non-empty).
  A bare string like `"SLACK_BOT_TOKEN"` is rejected with HTTP 400 — the schema
  requires the object form. `required`, `hint`, and `description` are optional.

:::warning Secret definitions cannot be plain strings
`{ "key": "X" }` (missing `label`) and `["X"]` (string form) both fail
validation. Always supply `{ "key": "...", "label": "..." }`.
:::

---

## Importing a module package

To create a module along with seed records and permission overrides in one call:

```http
POST /api/v1/instances/{instance_name}/modules/import
```

```json
{
  "schema_version": 1,
  "module": { "name": "leads", "label": "Leads", "fields": [ ... ] },
  "records": [ { "company": "Acme", "status": "new" } ],
  "permissions": { "delete_records": "approval" }
}
```

`schema_version` must be `1`. Reserved keys (`id`, `module_name`, `created_at`,
`updated_at`) are stripped from seeded records.

---

## Action invocation (HTTP)

These REST endpoints run an action's code directly. They are the operator-facing
path and do **not** apply the permission gate described below — see the
[Permissions](#permissions) section for where the three-level gate actually applies.

### Standalone (tool) action

```http
POST /api/v1/instances/{instance_name}/modules/{name}/action/{action_id}
```

Body — params may be sent flat or nested under `params` (both are accepted; the
router merges them):

```json
{ "params": { "channel": "#ops", "message": "hello" } }
```

### Record-scoped action

```http
POST /api/v1/instances/{instance_name}/modules/{name}/records/{record_id}/action/{action_id}
```

Record actions additionally receive the record dict in the execution context.

Before running, the router resolves the module's stored secrets and rejects the
call with HTTP 400 if any secret marked `required: true` is unset.

---

## Permissions

Module actions carry a three-level permission level:

- `allow` — execute normally
- `approval` — create an approval record and return **HTTP 202** with the pending approval
- `deny` — return **HTTP 403**

:::warning Where the permission gate applies
The three-level gate is enforced on the **agent tool path** — i.e. when the
agent invokes a module command through its tool registry (the dynamic-module
tool definitions built in `dynamic_modules.py`). The operator-facing REST
action endpoints (`POST .../action/{action_id}` and
`.../records/{id}/action/{action_id}`) execute the action code directly and do
**not** consult the permission level. Do not rely on `deny`/`approval` to block
a human operator hitting the REST endpoint; it only governs the agent.
:::

### Default permission levels

When a module is created or updated, permission rows are seeded for every reserved
record command and every custom action. Defaults:

| Command | Default level |
|---|---|
| `list_records` | `allow` |
| `get_record` | `allow` |
| `create_records` | `allow` |
| `update_records` | `allow` |
| `delete_records` | `approval` |
| `get_page` | `allow` |
| `edit_page` | `approval` |
| custom actions | `allow` (unless the action sets `permission_default`) |

Override any of these by passing a `permissions` object on create/update/import,
e.g. `{ "delete_records": "deny", "send_message": "approval" }`. Each value must
be one of `allow`, `approval`, `deny`; unknown command names are rejected with
HTTP 400.

When a module's actions change, permission rows for removed actions are deleted
and defaults are seeded for new ones; existing overrides are preserved.

---

## Execution context in action code

The executor injects these names into the action's namespace:

- `params` — dict of caller-supplied params (custom actions only receive the keys declared in `params`)
- `secrets` — dict of the module's resolved secrets (key → value)
- `record` — the record dict, for record-scoped actions only
- `http` — a shared `httpx.AsyncClient` (use `await http.get(...)`, 30s timeout)
- `result` — set this to a dict to return a custom response

Secrets are passed through the `secrets` dict, **not** via `os.environ`.

The following standard-library modules are pre-imported into the namespace, so
you can use them without an `import`: `json`, `re`, `math`, `base64`, `hashlib`,
`hmac`, `datetime`, `urllib`, and `os` (a hardened variant — see below).

The return value is whatever you assign to `result`, but only if it is a dict.
If `result` is not a dict (or unset), the executor returns `{"ok": True}`. If the
code raises, the executor returns `{"ok": False, "error": "<message>"}`.

```python
token = secrets.get("SLACK_BOT_TOKEN")
resp = await http.post(
    "https://slack.com/api/chat.postMessage",
    headers={"Authorization": f"Bearer {token}"},
    json={"channel": params["channel"], "text": params["message"]},
)
result = {"ok": resp.status_code == 200, "status": resp.status_code}
```

---

## Sandbox reality

The executor is a **permissive** sandbox, not a hard isolation boundary.

- A custom `__import__` blocks these top-level modules outright:
  `subprocess`, `pty`, `multiprocessing`, `ctypes`, `signal`.
- The injected `os` is a stripped copy with the process-spawning calls removed
  (`system`, `popen`, `exec*`, `spawn*`, `fork`, `forkpty`). Normal file I/O via
  `os` still works.
- Everything else in Python builtins is available, and network access via `http`
  is unrestricted.

:::warning Treat action code as privileged
Do not claim hard isolation. Action code can read and write the filesystem and
make arbitrary network calls. Review module code as you would any privileged
backend code before enabling it in production.
:::

---

## Updating a module

```http
PATCH /api/v1/instances/{instance_name}/modules/{name}
```

Editable fields (any subset; at least one required):

- `label`
- `icon`
- `fields`
- `fields_config`
- `actions`
- `secrets`
- `description`
- `order`
- `page_title`
- `page_content`

There is no `type` or `list_config` field. Permission overrides are supplied via
the separate top-level `permissions` object (not one of the editable fields
above). When `actions` change, permission rows for stale actions are removed and
defaults are seeded for new ones. The instance runtime context is rebuilt after
the update.

For surgical, agent-driven edits there is also an `edit_module` operation
(used by the `module_manager` system module) that applies the same primitives —
add/update/rename/remove field, set/patch/remove action, upsert/remove secret,
set permissions — so the UI `PATCH` path and the agent path cannot drift apart.

---

## Deleting a module

```http
DELETE /api/v1/instances/{instance_name}/modules/{name}
```

This deletes the module's records and secrets (FK constraints) and its permission
rows, then rebuilds the instance runtime context. System modules cannot be deleted.

---

## Quick validation checklist

After creating a module:

1. `GET /api/v1/instances/{instance_name}/modules/{name}` returns the expected schema.
2. Permission rows exist for the reserved commands and your custom actions
   (`delete_records` and `edit_page` default to `approval`).
3. A test action returns the expected `result` dict.
4. Configure any `required` secrets before running actions that depend on them —
   missing required secrets return HTTP 400.
5. If you intend the agent to gate an action, set its level via `permissions` and
   confirm the agent path returns 202 (`approval`) or 403 (`deny`). Remember the
   REST endpoints bypass this gate.
