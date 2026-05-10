# Unified Module System

Sentinel exposes modules as the main user-facing capability surface. Native
capabilities and user-created capabilities are both represented as modules.
The internal `ToolRegistry` still exists, but it is an adapter layer for the
agent/runtime loop, not a public product surface.

## Current Shape

- Public module API: `/api/modules`, mounted from
  `apps/backend/sentinel/app/routers/araios/modules.py`.
- System modules: code-defined modules under
  `apps/backend/sentinel/app/services/araios/system_modules/*/module.py`.
- Dynamic modules: DB-backed modules loaded by
  `apps/backend/sentinel/app/services/araios/dynamic_modules.py`.
- Tool adapter: modules are converted into `ToolDefinition` objects by
  `ModuleDefinition.to_tool_definitions()` in
  `apps/backend/sentinel/app/services/araios/module_types.py`.
- Runtime registry: system and dynamic module tools are merged by
  `apps/backend/sentinel/app/services/tools/runtime_registry.py`.
- The old `/api/v1/tools` HTTP surface is no longer mounted.

The broader application still has non-module APIs such as `/api/v1/sessions`,
`/api/v1/runtime`, `/api/v1/git`, and `/api/v1/telegram`. Modules are the
capability/control-plane surface, not the only API in the app.

## Module Model

```text
module
  name: string
  label: string
  description?: string
  icon?: string
  fields?: field[]
  fields_config?: object
  actions?: action[]
  secrets?: secret[]
  page_title?: string
  page_content?: string
  system?: bool
  order?: int
  grouped_tool?: bool
```

```text
action
  id: string
  label: string
  description?: string
  type?: "standalone" | "record"
  parameters_schema?: JSON Schema object
  params?: legacy flat parameter list
  code?: Python source for dynamic modules
  handler?: callable for system modules
  streaming?: bool
  approval?: bool
  permission_default?: "allow" | "approval" | "deny"
  requires_runtime_context?: bool
```

`params` is still accepted for compatibility and is converted into
`parameters_schema` on read. New code should use `parameters_schema`.

## System Modules

System modules are registered by
`apps/backend/sentinel/app/services/araios/system_modules/__init__.py`.
Each package exports a `MODULE` object.

Current system module packages include:

- `runtime_tool`, exposed as tool/module name `runtime`
- `python`
- `git_tool`, exposed as `git`
- `str_replace_editor`
- `http_request`
- `browser`
- `port_forward`
- `memory`
- `sub_agents`, exposed as `delegate`
- `telegram`
- `triggers`
- `module_manager`
- `tasks`
- `documents`
- `coordination`

System module actions store direct Python callables on `ActionDefinition.handler`.
There is no central `NATIVE_HANDLERS` string registry.

## Dynamic Modules

Dynamic modules are stored in the database and converted into grouped tools by
`build_dynamic_module_definition()`.

Every dynamic module automatically gets management commands:

- `list_records`
- `get_record`
- `create_records`
- `update_records`
- `delete_records`
- `get_page`
- `edit_page`

Bulk create, update, and delete are the canonical record mutation paths. Legacy
singular command aliases are kept only as compatibility shims.

Custom dynamic actions can include Python `code`. Today that code executes
in-process through `apps/backend/sentinel/app/services/araios/executor.py` with
a restricted import/builtin environment. It does not currently execute inside
the session runtime VM/container.

## Module To Tool Translation

The agent still consumes `ToolDefinition` objects. Translation happens at
registry-build time:

- Non-grouped modules with one action become one tool named after the module.
- Non-grouped modules with multiple actions become one tool per action using
  `{module}_{action}`.
- Grouped modules become one tool named after the module, with a `command`
  enum selecting the action.
- Approval checks are built from action permission defaults and DB permission
  overrides.
- Dynamic module tools are loaded alongside system module tools when the runtime
  registry is built or rebuilt.

The grouped-tool pattern is preferred for modules with many related actions
because it keeps the agent tool list smaller.

## Module Manager

`module_manager` is the agent-facing management/discovery module. It replaces
the older multi-tool bridge concepts.

It supports module discovery, module creation/deletion, record list/get/create/
update/delete, and action execution through one grouped tool.

## API Surface

Primary module routes:

```text
GET    /api/modules
POST   /api/modules
GET    /api/modules/{name}
PATCH  /api/modules/{name}
DELETE /api/modules/{name}

GET    /api/modules/{name}/records
POST   /api/modules/{name}/records
PATCH  /api/modules/{name}/records/{record_id}
DELETE /api/modules/{name}/records/{record_id}

POST   /api/modules/{name}/action/{action_id}
POST   /api/modules/{name}/records/{record_id}/action/{action_id}

PUT    /api/modules/{name}/secrets/{key}
DELETE /api/modules/{name}/secrets/{key}
GET    /api/modules/{name}/secrets-status
POST   /api/modules/import
```

Related module/control-plane routes remain mounted under `/api`:

- `/api/permissions`

## Frontend

`apps/frontend/sentinel/src/pages/ModulesPage.tsx` is the current module UI.
It displays system and dynamic modules in one surface. System modules are
read-only; dynamic modules can be created, edited, deleted, and used for
records/actions/pages/secrets.

There is no separate Tools page.

## Known Gaps

- Dynamic action Python runs in-process instead of inside the session runtime.
- Module/user-code streaming is not equivalent to runtime command streaming.
- Permission and approval behavior exists, but the policy surface still needs
  simplification.
- `ToolRegistry` remains a required internal adapter until the agent runtime can
  consume modules directly.
- Some compatibility route concepts and legacy names remain in tests/history and
  should not be reintroduced as public product concepts.
