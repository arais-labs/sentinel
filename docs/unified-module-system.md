# Unified Module System — Architecture Plan

## Goal

Replace the dual tool/module system with a single concept: **modules**. Every capability in the platform — native (runtime_exec, git, python) and user-created (CRM, weather checker) — is a module. The "tool" concept disappears from the data model and only exists as a translation layer for LLM compatibility.

---

## Current State (Problems)

1. **Two parallel systems**: Sentinel `ToolDefinition`/`ToolRegistry` (code) and AraiOS modules (DB)
2. **Three meta-tools** (`araios_modules`, `araios_records`, `araios_action`) bridge the gap
3. **Two API surfaces**: `/api/v1/tools` and `/api/modules`
4. **Two UI pages**: Tools page and Modules page
5. **Separate router trees**: `app/routers/araios/` and `app/routers/tools.py`
6. **Action limitations**: flat params, no streaming, no multi-path operations, no native handler routing

## Target State

- **One concept**: module
- **One API**: `/api/modules`
- **One UI page**: Modules
- **One registry**: merges code-defined system modules + DB user modules
- **"Tool"** exists only as an adapter that generates `ToolDefinition` structs for the agent loop

---

## Module Schema (Final)

```
module:
  name: string              # required — slug, unique, lowercase
  label: string             # required — display name
  description: string       # optional — what the agent and user see
  icon: string              # optional — lucide icon name, defaults to "box"

  fields:                   # optional — if present, module stores records
    - key: string           # required
      label: string         # required
      type: string          # required (text|textarea|email|url|number|date|select|badge|tags|readonly)
      required: bool        # optional, defaults to false
      options: string[]     # optional, only for select/badge

  fields_config:            # optional — UI display for record lists
    titleField: string
    subtitleField: string
    badgeField: string
    filterField: string
    metaField: string

  actions:                  # optional — executable capabilities
    - id: string                    # required
      label: string                 # required
      description: string           # optional
      type: "standalone" | "record" # optional, defaults to "standalone"
      parameters_schema: object     # full JSON Schema (replaces flat params)
      code: string                  # user modules — Python executed in runtime container
      handler: string               # system modules — native function name (mutually exclusive with code)
      approval: object              # optional — custom approval config
      streaming: bool               # optional — if true, output streams via WS

  secrets:                  # optional — runtime credentials
    - key: string           # required
      label: string         # required
      required: bool        # optional, defaults to false
      hint: string          # optional

  page_title: string        # optional — if set, module has a markdown page tab
  pinned: bool              # if true, actions registered in agent context always
  system: bool              # if true, defined in code, read-only in UI
```

### Key Changes from Current

| Before | After |
|--------|-------|
| `type: "data" \| "tool" \| "page"` | Gone — capabilities derived from what's defined |
| `placement: "standalone" \| "detail"` | `type: "standalone" \| "record"` on action |
| `params: [{key, label, type, required}]` | `parameters_schema: {JSON Schema}` |
| No native handler routing | `handler: string` on action |
| No streaming | `streaming: bool` on action |
| No multi-path operations | `parameters_schema` supports enums, conditionals |
| `is_system: bool` | `system: bool` — read-only in UI |

### Execution Routing

```
action has handler? → call native Python function (system module)
action has code?    → execute in runtime container via SSH (user module)
neither?            → error
```

### Streaming

- `streaming: true` + `handler` → native handler streams via WS (like runtime_exec today)
- `streaming: true` + `code` → runtime execution streams stdout via SSH → WS
- `streaming: false` → request-response, return dict

---

## System Modules (Code-Defined)

Defined in `app/services/araios/system_modules.py` as Python dicts. NOT stored in DB. Merged into the module list at runtime.

Each system module maps to existing native tool implementations:

| Module | Actions | Handler Functions |
|--------|---------|-------------------|
| `runtime_exec` | `run` | `_execute_via_ssh()` in builtin.py |
| `python` | `run` | `_run_python_in_runtime()` in builtin.py |
| `git_exec` | `run` | git_exec.py handler |
| `str_replace_editor` | `edit` (multi-path via `command` enum) | editor.py handler |
| `http_request` | `request` | http_request handler in builtin.py |
| `browser` | `navigate`, `screenshot`, `click`, `type`, `scroll`, `evaluate`, ... | browser tool handlers |
| `memory` | `store`, `roots`, `tree`, `get_node`, `list_children` | memory tool handlers |
| `sub_agents` | `spawn`, `check`, `list`, `cancel` | sub-agent handlers |
| `send_telegram_message` | `send` | telegram bridge handler |

### Handler Registry

```python
NATIVE_HANDLERS: dict[str, Callable] = {
    "runtime_exec.run": _execute_via_ssh,
    "python.run": _run_python_in_runtime,
    "git_exec.run": _git_exec_handler,
    "str_replace_editor.edit": _str_replace_handler,
    ...
}
```

When a system module action executes, the execution layer looks up `{module_name}.{action_id}` in `NATIVE_HANDLERS` and calls the function.

---

## Translation Layer: Module → ToolDefinition

The agent loop expects `ToolDefinition` objects. The translation layer generates these from modules:

```
For each pinned module:
  For each action in module.actions:
    If module has only one action:
      tool_name = module.name
    Else:
      tool_name = f"{module.name}_{action.id}"  # or keep as module.name with sub-commands

    ToolDefinition(
      name = tool_name,
      description = module.description + action.description,
      parameters_schema = action.parameters_schema,
      execute = route_to_handler_or_runtime(module, action),
      approval_gate = build_gate_from_action_approval(action),
    )
```

### Token Efficiency

Multi-action modules (like `str_replace_editor` with 5 commands) become ONE tool with a `command` enum in the schema — not 5 separate tools. The `parameters_schema` handles this natively via JSON Schema.

### Unpinned Module Discovery

Unpinned modules are NOT registered as tools. The agent accesses them via a single discovery meta-tool:

```
modules_discovery (always pinned):
  - list_modules: returns all modules with descriptions
  - get_module: returns full module config
  - list_records: returns records for a module
  - create_record / update_record / delete_record
  - run_action: execute an action on an unpinned module
```

This replaces `araios_modules`, `araios_records`, `araios_action` — one tool instead of three.

---

## API Surface

### Before (current)

```
/api/v1/tools                          — list native tools
/api/v1/tools/{name}                   — get native tool
/api/v1/tools/{name}/execute           — execute native tool
/api/modules                           — list araios modules
/api/modules/{name}                    — get module
/api/modules/{name}/records            — CRUD records
/api/modules/{name}/action/{id}        — execute action
/api/modules/{name}/records/{id}/action/{id} — record-scoped action
/api/approvals                         — approvals
/api/permissions                       — permissions
```

### After (unified)

```
/api/modules                           — list ALL modules (system + user)
/api/modules/{name}                    — get module
/api/modules/{name}/records            — CRUD records
/api/modules/{name}/action/{id}        — execute action (routes to handler or runtime)
/api/modules/{name}/records/{id}/action/{id} — record-scoped action
/api/approvals                         — approvals
/api/permissions                       — permissions
```

`/api/v1/tools` dies. `/api/v1/tools/{name}/execute` dies. Everything goes through modules.

---

## Frontend

### Sidebar (unified)

```
Sessions
Session Logs
Memory
Triggers
Modules          ← replaces both Tools and AraiOS Modules
Approvals
Permissions
Git
Telegram
Settings
```

### Modules Page

One grid showing all modules. System modules have a "system" badge and are not editable. User modules are fully editable. Click → tabs based on capabilities (Records, Actions, Page).

### Switcher

Dead. One app, one nav.

### Tools Page

Dead. Replaced by Modules page.

### Right Rail Modules Tab

Stays — session-scoped activity view.

---

## Implementation Phases

### Phase 1 — Action Schema Upgrade

**What:** Update action schema to support `parameters_schema`, `handler`, `streaming`, `approval`.

Files:
- `app/models/araios.py` — no model change needed (actions is JSON)
- `app/routers/araios/modules.py` — update serialization, validation
- `app/services/tools/araios_tools.py` — update tool descriptions
- SQL migration for `system` column on modules table
- Frontend `AraiOSPage.tsx` — render `parameters_schema` in action forms

### Phase 2 — System Module Definitions

**What:** Define all native tools as system module dicts in code. Create handler registry mapping `{module}.{action}` → native function.

Files:
- `app/services/araios/system_modules.py` — module definitions
- `app/services/araios/handlers.py` — handler registry + routing
- `app/routers/araios/modules.py` — merge system modules into list endpoint

### Phase 3 — Translation Layer

**What:** Replace `ToolRegistry` + `builtin.py` tool factory with module-based tool generation. Pinned modules auto-register as `ToolDefinition`. Execution routes through module system.

Files:
- `app/services/tools/module_adapter.py` — new: generates ToolDefinitions from modules
- `app/services/tools/builtin.py` — gut: remove individual tool factories
- `app/main.py` — wire module adapter into startup
- `app/services/agent/tool_adapter.py` — may need updates

### Phase 4 — Unify Discovery Tool

**What:** Replace `araios_modules` + `araios_records` + `araios_action` with one `modules_discovery` tool.

Files:
- `app/services/tools/araios_tools.py` — rewrite as single discovery tool
- `app/services/agent/policies.py` — update policy

### Phase 5 — Kill Old Tool System

**What:** Remove `/api/v1/tools`, `ToolsPage.tsx`, old tool router, app switcher.

Files:
- Delete `app/routers/tools.py`
- Delete `app/schemas/tools.py`
- Delete `apps/frontend/sentinel/src/pages/ToolsPage.tsx`
- `app/main.py` — remove tools router mount
- `App.tsx` — remove /tools route
- `AppShell.tsx` — remove switcher, flatten sidebar, remove Tools nav item, add Modules/Approvals/Permissions

### Phase 6 — Flatten Routes

**What:** Move module routes from `/api/modules` to be the canonical API. Remove `/api/v1/tools/*` references from tests and frontend.

---

## Risks & Open Questions

1. **Backward compatibility** — existing sessions have tool call history referencing `runtime_exec`, `git_exec`, etc. The translation layer must generate tools with the SAME names so history stays valid.

2. **Hot reload** — when a user creates/edits a pinned module, the tool registry needs to update without restart. Needs a mechanism to invalidate and regenerate.

3. **Parameter schema migration** — existing user module actions use flat `params`. Need a migration path to `parameters_schema` (or support both and translate flat params → JSON Schema on read).

4. **Streaming for user modules** — executing code in the runtime and streaming back via SSH → WS is new infrastructure. Can defer to a later phase.

5. **Approval evaluator registry** — mapping action `approval.evaluator` strings to Python functions needs a clean registry pattern.
