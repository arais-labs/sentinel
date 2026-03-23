from __future__ import annotations

from app.services.araios.module_types import (
    ActionDefinition,
    FieldDefinition,
    FieldsConfig,
    ModuleDefinition,
)

from .handlers import ALLOWED_COORDINATION_COMMANDS, handle_run


def _coordination_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": {
                "type": "string",
                "enum": list(ALLOWED_COORDINATION_COMMANDS),
                "description": "Coordination command: list or send.",
            },
            "agent": {"type": "string", "description": "Optional agent filter for list, or sender agent identifier for send."},
            "limit": {"type": "integer", "description": "Max number of messages to return for list."},
            "message": {"type": "string", "description": "Coordination message text for send."},
            "context": {"type": "object", "description": "Optional context metadata for send."},
        },
    }


MODULE = ModuleDefinition(
    name="coordination",
    label="Coordination",
    description="Inter-agent coordination log for sending and listing coordination messages between agents.",
    icon="message-circle",
    pinned=False,
    system=True,
    fields=[
        FieldDefinition(key="agent", label="Agent", type="text", required=True),
        FieldDefinition(key="message", label="Message", type="textarea", required=True),
        FieldDefinition(key="context", label="Context", type="readonly"),
    ],
    fields_config=FieldsConfig(
        titleField="agent",
        subtitleField="message",
    ),
    actions=[
        ActionDefinition(
            id="run",
            label="Coordination",
            description="Unified coordination entry point. Use command=list to read messages or command=send to append one.",
            handler=handle_run,
            parameters_schema=_coordination_parameters_schema(),
        )
    ],
)
