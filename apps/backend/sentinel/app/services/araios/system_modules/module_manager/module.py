from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import (
    handle_create_module,
    handle_create_records,
    handle_delete_module,
    handle_delete_records,
    handle_get_module,
    handle_get_record,
    handle_list_modules,
    handle_list_records,
    handle_run_action,
    handle_update_records,
)


def _name_prop() -> dict:
    return {"type": "string", "description": "Module name."}


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
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "label"],
        "properties": {
            "name": _name_prop(),
            "label": {"type": "string"},
            "description": {"type": "string"},
            "icon": {"type": "string"},
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["key", "label"],
                    "properties": {
                        "key": {"type": "string", "description": "snake_case field identifier"},
                        "label": {"type": "string", "description": "Human-readable field name"},
                        "type": {"type": "string", "enum": ["text", "textarea", "email", "url", "number", "date", "select", "tags", "readonly"]},
                        "required": {"type": "boolean"},
                        "options": {"type": "array", "items": {"type": "string"}, "description": "Only for type=select"},
                    },
                },
            },
            "fields_config": {"type": "object"},
            "actions": {"type": "array"},
            "permissions": {"type": "object"},
            "secrets": {"type": "array"},
            "page_title": {"type": "string"},
            "page_content": {"type": "string"},
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
                "Example: {\"key\": \"company\", \"label\": \"Company\", \"type\": \"text\", \"required\": true}. "
                "fields_config controls display: titleField (record title), subtitleField, badgeField (status chip), filterField (sidebar filter). "
                "All field keys referenced in fields_config must exist in fields."
            ),
            handler=handle_create_module,
            parameters_schema=_create_module_parameters_schema(),
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
