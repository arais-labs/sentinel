from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import (
    handle_create_module,
    handle_create_record,
    handle_delete_module,
    handle_delete_record,
    handle_get_module,
    handle_get_record,
    handle_list_modules,
    handle_list_records,
    handle_run_action,
    handle_update_record,
)


def _name_prop() -> dict:
    return {"type": "string", "description": "Module name."}


def _module_prop() -> dict:
    return {"type": "string", "description": "Target module name."}


def _record_id_prop() -> dict:
    return {"type": "string", "description": "Record ID."}


def _session_id_prop() -> dict:
    return {"type": "string", "description": "Current session ID."}


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
            "fields": {"type": "array"},
            "fields_config": {"type": "object"},
            "actions": {"type": "array"},
            "secrets": {"type": "array"},
            "page_title": {"type": "string"},
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


def _create_record_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["module", "data"],
        "properties": {
            "module": _module_prop(),
            "data": {"type": "object"},
        },
    }


def _update_record_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["module", "record_id", "data"],
        "properties": {
            "module": _module_prop(),
            "record_id": _record_id_prop(),
            "data": {"type": "object"},
        },
    }


def _delete_record_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["module", "record_id"],
        "properties": {
            "module": _module_prop(),
            "record_id": _record_id_prop(),
        },
    }


def _run_action_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["module", "action_id", "session_id"],
        "properties": {
            "module": _module_prop(),
            "record_id": _record_id_prop(),
            "action_id": {"type": "string"},
            "params": {"type": "object"},
            "session_id": _session_id_prop(),
        },
    }


MODULE = ModuleDefinition(
    name="module_manager",
    label="Module Manager",
    description="Unified araiOS module engine for module CRUD, record CRUD, and action execution.",
    icon="boxes",
    pinned=False,
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="list_modules",
            label="List Modules",
            description="List all araiOS modules.",
            handler=handle_list_modules,
            parameters_schema=_list_modules_parameters_schema(),
        ),
        ActionDefinition(
            id="get_module",
            label="Get Module",
            description="Get one araiOS module by name.",
            handler=handle_get_module,
            parameters_schema=_get_module_parameters_schema(),
        ),
        ActionDefinition(
            id="create_module",
            label="Create Module",
            description="Create a new araiOS module.",
            handler=handle_create_module,
            parameters_schema=_create_module_parameters_schema(),
        ),
        ActionDefinition(
            id="delete_module",
            label="Delete Module",
            description="Delete one araiOS module and its related data.",
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
            id="create_record",
            label="Create Record",
            description="Create a new record in a module.",
            handler=handle_create_record,
            parameters_schema=_create_record_parameters_schema(),
        ),
        ActionDefinition(
            id="update_record",
            label="Update Record",
            description="Update one record in a module.",
            handler=handle_update_record,
            parameters_schema=_update_record_parameters_schema(),
        ),
        ActionDefinition(
            id="delete_record",
            label="Delete Record",
            description="Delete one record from a module.",
            handler=handle_delete_record,
            parameters_schema=_delete_record_parameters_schema(),
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
