from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import handle_close, handle_list, handle_open


def _open_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["port"],
        "properties": {
            "port": {
                "type": "integer",
                "description": "HTTP or WebSocket service port inside the runtime, usually bound on localhost.",
            },
            "host": {
                "type": "string",
                "description": "Target host inside the runtime. Default is 127.0.0.1.",
            },
            "label": {
                "type": "string",
                "description": "Optional short label for this forward, such as Vite preview or FastAPI docs.",
            },
            "protocol": {
                "type": "string",
                "description": "Forward mode. Use http for web apps and WebSocket apps, or tcp for arbitrary raw TCP services.",
                "enum": ["http", "tcp"],
            },
        },
    }


def _close_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["forward_id"],
        "properties": {
            "forward_id": {
                "type": "string",
                "description": "Runtime forward ID to close.",
            },
        },
    }


MODULE = ModuleDefinition(
    name="port_forward",
    label="Port Forward",
    description=(
        "Expose a service running inside the session runtime on a real host port. "
        "Use http for web apps and WebSocket apps, or tcp for arbitrary raw TCP services like databases, Redis, or custom servers. "
        "The returned result includes the direct host endpoint to open or connect to."
    ),
    icon="link",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="open",
            label="Open Forward",
            description="Open or reuse a session-scoped forward for one runtime service and return a direct host endpoint.",
            handler=handle_open,
            requires_runtime_context=True,
            parameters_schema=_open_parameters_schema(),
        ),
        ActionDefinition(
            id="list",
            label="List Forwards",
            description="List currently open forwards for the shared runtime session.",
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
            description="Close one open runtime forward when it is no longer needed.",
            handler=handle_close,
            requires_runtime_context=True,
            parameters_schema=_close_parameters_schema(),
        ),
    ],
)
