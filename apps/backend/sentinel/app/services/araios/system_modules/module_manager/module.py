from __future__ import annotations

from app.schemas.modules import (
    module_create_tool_parameters_schema,
    module_name_parameter_schema,
)
from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import (
    handle_create_module,
    handle_create_records,
    handle_delete_module,
    handle_delete_records,
    handle_edit_module,
    handle_get_module,
    handle_get_record,
    handle_list_modules,
    handle_list_records,
    handle_run_action,
    handle_update_records,
)


def _name_prop() -> dict:
    return module_name_parameter_schema()


def _module_prop() -> dict:
    return {"type": "string", "description": "Target module name."}


def _record_id_prop() -> dict:
    return {"type": "string", "description": "Record ID."}


def _list_modules_parameters_schema() -> dict:
    return {"type": "object", "additionalProperties": False, "properties": {}}


def _get_module_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["name"],
        "properties": {
            "name": _name_prop(),
        },
    }


def _create_module_parameters_schema() -> dict:
    return module_create_tool_parameters_schema()


_EDIT_MODULE_OPS = [
    "set_meta",
    "add_field",
    "update_field",
    "rename_field",
    "remove_field",
    "set_action",
    "patch_action",
    "remove_action",
    "set_fields_config",
    "patch_fields_config",
    "upsert_secret",
    "remove_secret",
    "set_permissions",
]


def _edit_module_parameters_schema() -> dict:
    # All per-op fields live INSIDE ops.items so they never participate in the grouped-tool
    # top-level property merge (module_types._build_grouped_parameters_schema). The only new
    # top-level keys are 'name' (reused verbatim) and 'ops'. Per-op shape is enforced by the
    # EditModuleRequest Pydantic model at execution time, not by the shallow runtime validator.
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "ops"],
        "properties": {
            "name": _name_prop(),
            "ops": {
                "type": "array",
                "minItems": 1,
                "description": "Ordered list of edit operations, applied atomically (all-or-nothing).",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["op"],
                    "properties": {
                        "op": {"type": "string", "enum": list(_EDIT_MODULE_OPS)},
                        # set_meta
                        "label": {"type": "string"},
                        "icon": {"type": "string"},
                        "description": {"type": "string"},
                        "order": {"type": "integer"},
                        "page_title": {"type": "string"},
                        "page_content": {"type": "string"},
                        # field ops
                        "field": {
                            "type": "object",
                            "description": "Field object {key,label,type,required,options} for add_field.",
                        },
                        "position": {"type": "integer"},
                        "key": {"type": "string"},
                        "changes": {
                            "type": "object",
                            "description": "Field props to overwrite for update_field (cannot include 'key').",
                        },
                        "from_key": {"type": "string"},
                        "to_key": {"type": "string"},
                        "migrate_record_data": {"type": "boolean"},
                        "purge_record_data": {
                            "type": "boolean",
                            "description": "remove_field: if true, ALSO delete that field's data from every record. Warn the user first.",
                        },
                        # action ops
                        "action": {
                            "type": "object",
                            "description": "Full action object {id,label,code,type,params,...} for set_action.",
                        },
                        "id": {"type": "string"},
                        "set": {
                            "type": "object",
                            "description": "patch_action: keys to overwrite (e.g. {code: '...'}); omitted keys are preserved.",
                        },
                        # fields_config ops
                        "fields_config": {"type": "object"},
                        "config": {"type": "object"},
                        # secret ops
                        "secret": {
                            "type": "object",
                            "description": "Secret definition {key,label,required,hint} for upsert_secret.",
                        },
                        "purge_value": {
                            "type": "boolean",
                            "description": "remove_secret: if true, also delete the stored secret value.",
                        },
                        # set_permissions
                        "permissions": {
                            "type": "object",
                            "description": "Map of command -> allow|approval|deny.",
                        },
                    },
                },
            },
        },
    }


def _delete_module_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["name"],
        "properties": {
            "name": _name_prop(),
        },
    }


def _list_records_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["module"],
        "properties": {
            "module": _module_prop(),
        },
    }


def _get_record_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["module", "record_id"],
        "properties": {
            "module": _module_prop(),
            "record_id": _record_id_prop(),
        },
    }


def _create_records_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["module", "records"],
        "properties": {
            "module": _module_prop(),
            "records": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "object"},
            },
        },
    }


def _update_records_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["module", "updates"],
        "properties": {
            "module": _module_prop(),
            "updates": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["record_id", "data"],
                    "properties": {
                        "record_id": _record_id_prop(),
                        "data": {"type": "object"},
                    },
                },
            },
        },
    }


def _delete_records_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["module", "record_ids"],
        "properties": {
            "module": _module_prop(),
            "record_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string"},
            },
        },
    }


def _run_action_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["module", "action_id"],
        "properties": {
            "module": _module_prop(),
            "record_id": _record_id_prop(),
            "action_id": {"type": "string"},
            "params": {"type": "object"},
        },
    }


MODULE = ModuleDefinition(
    name="module_manager",
    label="Module Manager",
    description="Unified dynamic module engine for module CRUD, record CRUD, and action execution.",
    icon="boxes",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="list_modules",
            label="List Modules",
            description="List all dynamic modules.",
            handler=handle_list_modules,
            parameters_schema=_list_modules_parameters_schema(),
        ),
        ActionDefinition(
            id="get_module",
            label="Get Module",
            description="Get one dynamic module by name.",
            handler=handle_get_module,
            parameters_schema=_get_module_parameters_schema(),
        ),
        ActionDefinition(
            id="create_module",
            label="Create Module",
            description=(
                "Create a new dynamic module. "
                "IMPORTANT: 'fields' must be an array of field OBJECTS — never plain strings. "
                "Each field object requires 'key' (snake_case string) and 'label' (human-readable string). "
                "Optional per-field: 'type' (text|textarea|email|url|number|date|select|tags, default: text), "
                "'required' (boolean), 'options' (array of strings, only for type=select). "
                'Example: {"key": "company", "label": "Company", "type": "text", "required": true}. '
                "fields_config controls display: titleField (record title), subtitleField, badgeField (status chip), filterField (sidebar filter). "
                "All field keys referenced in fields_config must exist in fields. "
                "To CHANGE an EXISTING module, use command=edit_module — never delete+recreate "
                "(that destroys records, secret values, and permissions)."
            ),
            handler=handle_create_module,
            parameters_schema=_create_module_parameters_schema(),
        ),
        ActionDefinition(
            id="edit_module",
            label="Edit Module",
            description=(
                "Apply surgical edits to an EXISTING module in one atomic (all-or-nothing) call. "
                "Prefer this over delete+recreate, which destroys records, secret values, and permissions. "
                "'ops' is an ordered list; each op object has an 'op' key. Key idioms: "
                "fix one action's code → {op: 'patch_action', id: '<action_id>', set: {code: '...'}} "
                "(only keys in 'set' change; omit a key to PRESERVE it; params/parameters_schema replace wholesale). "
                "Add a field → {op: 'add_field', field: {key, label, type, ...}} (do NOT re-send the other fields). "
                "Rename a field → {op: 'rename_field', from_key, to_key} (record data migrates by default; "
                "set migrate_record_data: false to skip). "
                "Remove a field → {op: 'remove_field', key} leaves existing record data intact; pass "
                "purge_record_data: true to ALSO delete that data from every record — WARN THE USER before purging. "
                "Other ops: set_meta (label/icon/description/order/page_title/page_content), "
                "set_action (add/replace a whole action), remove_action, set_fields_config, patch_fields_config, "
                "upsert_secret, remove_secret (purge_value: true also deletes the stored value), set_permissions "
                "(command → allow|approval|deny). The module name is immutable; all fields_config refs must point to "
                "existing fields."
            ),
            handler=handle_edit_module,
            approval=True,
            parameters_schema=_edit_module_parameters_schema(),
        ),
        ActionDefinition(
            id="delete_module",
            label="Delete Module",
            description="Delete one dynamic module and its related data.",
            handler=handle_delete_module,
            approval=True,
            parameters_schema=_delete_module_parameters_schema(),
        ),
        ActionDefinition(
            id="list_records",
            label="List Records",
            description="List records for a module.",
            handler=handle_list_records,
            parameters_schema=_list_records_parameters_schema(),
        ),
        ActionDefinition(
            id="get_record",
            label="Get Record",
            description="Get one record from a module.",
            handler=handle_get_record,
            parameters_schema=_get_record_parameters_schema(),
        ),
        ActionDefinition(
            id="create_records",
            label="Create Records",
            description="Create one or more records in a module.",
            handler=handle_create_records,
            parameters_schema=_create_records_parameters_schema(),
        ),
        ActionDefinition(
            id="update_records",
            label="Update Records",
            description="Update one or more records in a module.",
            handler=handle_update_records,
            parameters_schema=_update_records_parameters_schema(),
        ),
        ActionDefinition(
            id="delete_records",
            label="Delete Records",
            description="Delete one or more records from a module.",
            handler=handle_delete_records,
            parameters_schema=_delete_records_parameters_schema(),
        ),
        ActionDefinition(
            id="run_action",
            label="Run Module Action",
            description="Execute a module action for a module or record.",
            handler=handle_run_action,
            approval=True,
            parameters_schema=_run_action_parameters_schema(),
        ),
    ],
)
