from __future__ import annotations

from app.services.araios.module_types import (
    ActionDefinition,
    FieldDefinition,
    FieldsConfig,
    ModuleDefinition,
)

from .handlers import handle_list, handle_send


def _agent_prop() -> dict:
    return {
        "type": "string",
        "description": "Optional agent filter for list, or sender agent identifier for send.",
    }


def _list_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "agent": _agent_prop(),
            "limit": {"type": "integer", "description": "Max number of messages to return for list."},
        },
    }


def _send_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["agent", "message"],
        "properties": {
            "agent": _agent_prop(),
            "message": {"type": "string", "description": "Coordination message text for send."},
            "context": {"type": "object", "description": "Optional context metadata for send."},
        },
    }


MODULE = ModuleDefinition(
    name="coordination",
    label="Coordination",
    description="Inter-agent coordination log for sending and listing coordination messages between agents.",
    icon="message-circle",
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
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="list",
            label="List Coordination Messages",
            description="Read coordination messages, optionally filtered by agent.",
            handler=handle_list,
            parameters_schema=_list_parameters_schema(),
        ),
        ActionDefinition(
            id="send",
            label="Send Coordination Message",
            description="Append one coordination message for an agent.",
            handler=handle_send,
            parameters_schema=_send_parameters_schema(),
        ),
    ],
)
