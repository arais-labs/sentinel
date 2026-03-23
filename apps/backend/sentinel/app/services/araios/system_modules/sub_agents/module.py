from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import ALLOWED_SUB_AGENT_COMMANDS, handle_run


def _sub_agents_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["command"],
        "properties": {
            "command": {
                "type": "string",
                "enum": list(ALLOWED_SUB_AGENT_COMMANDS),
                "description": "Sub-agent command: spawn, check, list, or cancel.",
            },
            "session_id": {"type": "string", "description": "Current session ID for spawn, list, or cancel."},
            "task_id": {"type": "string", "description": "Sub-agent task ID for check or cancel."},
            "objective": {
                "type": "string",
                "description": "Concrete one-off outcome the sub-agent should produce for spawn.",
            },
            "scope": {
                "type": "string",
                "description": "Extra context or constraints for the sub-agent for spawn.",
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional allowlist of tool names for spawn. Omit or pass [] to allow all tools.",
            },
            "browser_tab_id": {
                "type": "string",
                "description": "Optional browser tab ID to pin sub-agent browser actions to one tab for spawn.",
            },
            "max_steps": {
                "type": "integer",
                "description": "Maximum iterations for spawn (default 10, max 50).",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Timeout in seconds for spawn (default 300).",
            },
        },
    }


MODULE = ModuleDefinition(
    name="sub_agents",
    label="Sub-Agents",
    description=(
        "Spawn bounded sub-agent tasks for delegation, check their status, "
        "list active tasks, and cancel running sub-agents."
    ),
    icon="users",
    pinned=False,
    system=True,
    actions=[
        ActionDefinition(
            id="run",
            label="Sub-Agents",
            description=(
                "Unified sub-agent delegation entry point. Use command=spawn, check, list, or cancel."
            ),
            handler=handle_run,
            parameters_schema=_sub_agents_parameters_schema(),
        )
    ],
)
