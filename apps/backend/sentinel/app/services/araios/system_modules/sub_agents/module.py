from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import handle_cancel, handle_list, handle_spawn, handle_status


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
                "description": "Concrete delegated outcome the sub-agent should produce, such as investigating one candidate, checking one surface, or validating one branch of work.",
            },
            "scope": {
                "type": "string",
                "description": "Extra context, boundaries, or success criteria for that delegated branch.",
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional allowlist of tool names. Omit or pass [] to allow all tools.",
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


def _status_parameters_schema() -> dict:
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
        "properties": {},
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
    name="delegate",
    label="Delegate",
    description=(
        "Delegate independent branches of work to sub-agents. Use this instead of doing multiple exploratory or status-checking tool calls yourself when a task can be split into parallel investigations, candidate exploration, verification, or other bounded subproblems, then review results and integrate them in the main loop."
    ),
    icon="users",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="spawn",
            label="Spawn Delegated Branch",
            description="Spawn one bounded delegated branch, especially for parallel investigation, candidate exploration, isolated execution, or verification. This should be the default first move when independent branches are clear. After spawning, do not normally request status immediately; let it run and either end the turn or do distinct non-overlapping work. The parent session will be prompted automatically when the delegated branch finishes.",
            handler=handle_spawn,
            requires_runtime_context=True,
            parameters_schema=_spawn_parameters_schema(),
        ),
        ActionDefinition(
            id="status",
            label="Delegated Branch Status",
            description="Get the status and current result of one delegated branch only when you actually need it now. Not for immediate post-spawn polling in the normal case, because the parent session will be prompted automatically when the delegated branch finishes.",
            handler=handle_status,
            parameters_schema=_status_parameters_schema(),
        ),
        ActionDefinition(
            id="list",
            label="List Delegated Branches",
            description="List delegated branches for the current session when you need to inspect work already in flight or avoid overlap.",
            handler=handle_list,
            requires_runtime_context=True,
            parameters_schema=_list_parameters_schema(),
        ),
        ActionDefinition(
            id="cancel",
            label="Cancel Delegated Branch",
            description="Cancel a delegated branch that is no longer needed or should be retried with a better scope.",
            handler=handle_cancel,
            requires_runtime_context=True,
            parameters_schema=_cancel_parameters_schema(),
        ),
    ],
)
