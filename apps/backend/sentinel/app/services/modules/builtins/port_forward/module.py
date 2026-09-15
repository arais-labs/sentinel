from __future__ import annotations

from app.services.modules.definitions import ActionDefinition, ModuleDefinition

from .handlers import handle_close, handle_list, handle_open

MODULE = ModuleDefinition(
    name="port_forward",
    label="Port Forward",
    description=(
        "Let the user view a web app running inside the attached workspace. Start the server first, "
        "then open its container port. Present the returned url unchanged as a Markdown link; "
        "clicking it opens a separate Sentinel preview window, including assets and WebSockets. "
        "This does not start the server or publish it to the internet. A localhost curl checks only "
        "the server, not the user-facing preview; do not claim the preview was verified unless you tested it."
    ),
    icon="link",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="open",
            label="Open Forward",
            description="Open or reuse a session-scoped proxy for one workspace web service.",
            handler=handle_open,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["port"],
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "Service port inside the workspace container, reachable on localhost.",
                    },
                    "host": {
                        "type": "string",
                        "description": "Host inside the workspace container. Only 127.0.0.1 or localhost is allowed.",
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
            description="List active forwards for this session's attached workspace.",
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
