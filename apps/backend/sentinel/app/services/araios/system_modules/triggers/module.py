from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import ALLOWED_TRIGGER_COMMANDS, handle_run


def _triggers_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": {
                "type": "string",
                "enum": list(ALLOWED_TRIGGER_COMMANDS),
                "description": "Trigger command: create, list, update, or delete.",
            },
            "session_id": {"type": "string"},
            "trigger_id": {"type": "string"},
            "name": {"type": "string", "description": "Trigger name."},
            "type": {"type": "string", "enum": ["cron", "heartbeat"]},
            "config": {
                "type": "object",
                "description": "For cron: {expr: '0 9 * * MON-FRI'}. For heartbeat: {interval_seconds: 3600}.",
            },
            "action_type": {"type": "string", "enum": ["agent_message", "tool_call", "http_request"]},
            "action_config": {
                "type": "object",
                "description": "For agent_message: {message, route_mode, target_session_id}.",
            },
            "enabled": {"type": "boolean"},
            "enabled_only": {"type": "boolean"},
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
    actions=[
        ActionDefinition(
            id="run",
            label="Trigger Command",
            description=(
                "Single trigger entry point. Choose the operation with command=create, list, update, or delete."
            ),
            handler=handle_run,
            parameters_schema=_triggers_parameters_schema(),
        )
    ],
)
