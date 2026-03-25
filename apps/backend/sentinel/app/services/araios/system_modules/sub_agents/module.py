from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import handle_cancel, handle_check, handle_list, handle_spawn
def _task_id_prop() -> dict:
    return {"type": "string", "description": "Sub-agent task ID."}


def _spawn_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["objective"],
        "properties": {
            "objective": {
                "type": "string",
                "description": "Concrete one-off outcome the sub-agent should produce.",
            },
            "scope": {
                "type": "string",
                "description": "Extra context or constraints for the sub-agent.",
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional allowlist of tool names. Omit or pass [] to allow all tools.",
            },
            "browser_tab_id": {
                "type": "string",
                "description": "Optional browser tab ID to pin sub-agent browser actions to one tab.",
            },
            "max_steps": {
                "type": "integer",
                "description": "Maximum iterations (default 10, max 50).",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Timeout in seconds (default 300).",
            },
        },
    }


def _check_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id"],
        "properties": {
            "task_id": _task_id_prop(),
        },
    }


def _list_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {
        },
    }


def _cancel_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["task_id"],
        "properties": {
            "task_id": _task_id_prop(),
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
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="spawn",
            label="Spawn Sub-Agent",
            description="Spawn one bounded sub-agent task.",
            handler=handle_spawn,
            requires_runtime_context=True,
            parameters_schema=_spawn_parameters_schema(),
        ),
        ActionDefinition(
            id="check",
            label="Check Sub-Agent",
            description="Check the status of one sub-agent task.",
            handler=handle_check,
            parameters_schema=_check_parameters_schema(),
        ),
        ActionDefinition(
            id="list",
            label="List Sub-Agents",
            description="List sub-agent tasks for the current session.",
            handler=handle_list,
            requires_runtime_context=True,
            parameters_schema=_list_parameters_schema(),
        ),
        ActionDefinition(
            id="cancel",
            label="Cancel Sub-Agent",
            description="Cancel a running sub-agent task for the current session.",
            handler=handle_cancel,
            requires_runtime_context=True,
            parameters_schema=_cancel_parameters_schema(),
        ),
    ],
)
