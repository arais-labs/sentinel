from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import handle_terminal_close, handle_terminal_list, handle_terminal_read, handle_user

_TERMINAL_ID_PROPERTY: dict = {
    "type": "string",
    "description": "Terminal id. '0' is the prioritized main terminal but can still be closed if requested.",
}

_TERMINAL_IDS_PROPERTY: dict = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Terminal ids. Use this to read or close multiple terminals in one call.",
}


MODULE = ModuleDefinition(
    name="runtime",
    label="Runtime",
    description="Run shell commands in the session workspace through the SSH/tmux runtime.",
    icon="terminal",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="user",
            label="Run Shell Command",
            description=(
                "Run a shell command in the session runtime workspace. Commands execute inside "
                "the session's tmux-backed OS sandbox."
            ),
            handler=handle_user,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["shell_command"],
                "properties": {
                    "shell_command": {
                        "type": "string",
                        "description": "Shell command to run.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional working directory inside the sandbox, for example /workspace.",
                    },
                    "terminal_id": _TERMINAL_ID_PROPERTY,
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Command timeout in seconds. Defaults to 300.",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "Run the command in a non-primary terminal and return immediately.",
                    },
                    "env": {
                        "type": "object",
                        "description": "Optional environment variables for this command.",
                    },
                },
            },
        ),
        ActionDefinition(
            id="terminal_list",
            label="List Terminals",
            description="List active tmux-backed terminals for this session. Optionally filter with terminal_ids.",
            handler=handle_terminal_list,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "terminal_id": _TERMINAL_ID_PROPERTY,
                    "terminal_ids": _TERMINAL_IDS_PROPERTY,
                },
            },
        ),
        ActionDefinition(
            id="terminal_read",
            label="Read Terminals",
            description=(
                "Read recent ANSI-stripped output from one or more terminals. "
                "Use for progress checks, not for polling background job completion."
            ),
            handler=handle_terminal_read,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "terminal_id": _TERMINAL_ID_PROPERTY,
                    "terminal_ids": _TERMINAL_IDS_PROPERTY,
                    "tail_bytes": {
                        "type": "integer",
                        "description": "Trailing bytes per terminal. Defaults to 2000.",
                    },
                },
            },
        ),
        ActionDefinition(
            id="terminal_close",
            label="Close Terminals",
            description=(
                "Close one or more tmux-backed terminals. Closing terminal '0' is allowed; "
                "it is just the prioritized main terminal and will be recreated on demand."
            ),
            handler=handle_terminal_close,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "terminal_id": _TERMINAL_ID_PROPERTY,
                    "terminal_ids": _TERMINAL_IDS_PROPERTY,
                },
            },
        ),
    ],
)
