from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import handle_create, handle_delete, handle_list, handle_update


def _session_id_prop() -> dict:
    return {"type": "string", "description": "Context session ID used to scope trigger ownership."}


def _trigger_id_prop() -> dict:
    return {"type": "string", "description": "Trigger UUID."}


def _name_prop() -> dict:
    return {"type": "string", "description": "Trigger name."}


def _type_prop() -> dict:
    return {"type": "string", "enum": ["cron", "heartbeat"], "description": "Trigger type."}


def _config_prop() -> dict:
    return {
        "type": "object",
        "description": "For cron: {expr: '0 9 * * MON-FRI'}. For heartbeat: {interval_seconds: 3600}.",
    }


def _action_type_prop() -> dict:
    return {
        "type": "string",
        "enum": ["agent_message", "tool_call", "http_request"],
        "description": "Action kind to execute when the trigger fires.",
    }


def _action_config_prop() -> dict:
    return {
        "type": "object",
        "description": "For agent_message: {message, route_mode, target_session_id}.",
    }


def _enabled_prop() -> dict:
    return {"type": "boolean", "description": "Whether the trigger is enabled."}


def _create_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "type", "config", "action_type", "action_config"],
        "properties": {
            "session_id": _session_id_prop(),
            "name": _name_prop(),
            "type": _type_prop(),
            "config": _config_prop(),
            "action_type": _action_type_prop(),
            "action_config": _action_config_prop(),
            "enabled": _enabled_prop(),
        },
    }


def _list_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id"],
        "properties": {
            "session_id": _session_id_prop(),
            "enabled_only": {"type": "boolean", "description": "If true, only enabled triggers are returned."},
        },
    }


def _update_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", "trigger_id"],
        "properties": {
            "session_id": _session_id_prop(),
            "trigger_id": _trigger_id_prop(),
            "name": _name_prop(),
            "config": _config_prop(),
            "action_config": _action_config_prop(),
            "enabled": _enabled_prop(),
        },
    }


def _delete_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", "trigger_id"],
        "properties": {
            "session_id": _session_id_prop(),
            "trigger_id": _trigger_id_prop(),
        },
    }


MODULE = ModuleDefinition(
    name="triggers",
    label="Triggers",
    description=(
        "Create and manage scheduled triggers that automatically run agent messages "
        "or tool calls on a cron schedule or heartbeat interval."
    ),
    icon="zap",
    pinned=True,
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="create",
            label="Create Trigger",
            description="Create a new scheduled trigger.",
            handler=handle_create,
            parameters_schema=_create_parameters_schema(),
        ),
        ActionDefinition(
            id="list",
            label="List Triggers",
            description="List triggers visible to the current session owner.",
            handler=handle_list,
            parameters_schema=_list_parameters_schema(),
        ),
        ActionDefinition(
            id="update",
            label="Update Trigger",
            description="Update an existing trigger by ID.",
            handler=handle_update,
            parameters_schema=_update_parameters_schema(),
        ),
        ActionDefinition(
            id="delete",
            label="Delete Trigger",
            description="Delete an existing trigger by ID.",
            handler=handle_delete,
            parameters_schema=_delete_parameters_schema(),
        ),
    ],
)
