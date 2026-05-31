from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import handle_close, handle_list, handle_open

MODULE = ModuleDefinition(
    name="port_forward",
    label="Port Forward",
    description=(
        "Expose HTTP/WebSocket services running on loopback inside the SSH runtime "
        "through an authenticated Sentinel proxy URL."
    ),
    icon="link",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="open",
            label="Open Forward",
            description="Open or reuse a session-scoped proxy for one runtime web service.",
            handler=handle_open,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["port"],
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Service port inside the runtime, usually bound on localhost.",
                    },
                    "host": {
                        "type": "string",
                        "description": "Runtime host. V1 only allows 127.0.0.1 or localhost.",
                    },
                    "protocol": {
                        "type": "string",
                        "description": "Forwarded web protocol.",
                        "enum": ["http", "websocket", "ws"],
                    },
                    "label": {
                        "type": "string",
                        "description": "Optional short label, for example Vite preview.",
                    },
                },
            },
        ),
        ActionDefinition(
            id="list",
            label="List Forwards",
            description="List active forwards for the session runtime workspace.",
            handler=handle_list,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
        ),
        ActionDefinition(
            id="close",
            label="Close Forward",
            description="Close one active forward.",
            handler=handle_close,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["forward_id"],
                "properties": {
                    "forward_id": {
                        "type": "string",
                        "description": "Forward id returned by port_forward.open.",
                    },
                },
            },
        ),
    ],
)
