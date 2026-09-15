from __future__ import annotations

from app.services.modules.definitions import ActionDefinition, ModuleDefinition
from app.services.agent.policies import DELEGATION_POLICY

from .handlers import (
    handle_cancel,
    handle_list,
    handle_resume,
    handle_spawn,
    handle_status,
)


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
            "tier": {
                "type": "string",
                "enum": ["fast", "normal", "hard"],
                "description": "Optional model tier override. Omit to inherit the parent's model and reasoning selection.",
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
    description=DELEGATION_POLICY,
    icon="users",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="spawn",
            label="Spawn Delegated Branch",
            description="Delegate an independent objective to a fully capable peer. "
            + DELEGATION_POLICY,
            handler=handle_spawn,
            requires_runtime_context=True,
            parameters_schema=_spawn_parameters_schema(),
        ),
        ActionDefinition(
            id="status",
            label="Delegated Branch Status",
            description="Get the status and current result of one delegated branch only when you actually need it now. Not for immediate post-spawn polling in the normal case, because the parent session will be prompted automatically when the delegated branch finishes.",
            handler=handle_status,
            requires_runtime_context=True,
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
            id="resume",
            label="Resume Delegated Branch",
            description="Continue a cancelled, failed, or completed sub-agent in its existing child conversation. "
            "Preserves its context, objective, tools, and cumulative usage. Supply current instructions; "
            "the agent should check interrupted work before continuing. Does not replay interrupted tool calls. "
            "Use only when continuing the user's task is authorized; stopping a run never auto-resumes children.",
            handler=handle_resume,
            requires_runtime_context=True,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["task_id", "message"],
                "properties": {
                    "task_id": _task_id_prop(),
                    "message": {
                        "type": "string",
                        "minLength": 1,
                        "description": "What to continue, including changed context and any checks needed after interruption.",
                    },
                },
            },
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
