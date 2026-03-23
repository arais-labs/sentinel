from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ApprovalDefinition, ModuleDefinition

from .handlers import (
    _runtime_exec_approval_evaluator,
    handle_job_logs,
    handle_job_status,
    handle_job_stop,
    handle_jobs_list,
    handle_run,
)


def _session_id_prop() -> dict:
    return {"type": "string", "description": "Current session ID."}


def _job_id_prop() -> dict:
    return {"type": "string", "description": "Detached job ID."}


def _run_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", "shell_command"],
        "properties": {
            "session_id": _session_id_prop(),
            "shell_command": {"type": "string", "description": "Shell command to execute."},
            "privilege": {
                "type": "string",
                "enum": ["user", "root"],
                "description": "Execution privilege mode (default user). root requires approval.",
            },
            "cwd": {"type": "string", "description": "Working directory inside the session workspace."},
            "env": {"type": "object", "description": "Environment variable overrides."},
            "timeout_seconds": {"type": "integer", "description": "Execution timeout in seconds (default 300, max 1800)."},
            "approval_timeout_seconds": {"type": "integer", "description": "Root approval wait timeout in seconds (default 600, max 3600)."},
            "detached": {"type": "boolean", "description": "Run in the background as a tracked job."},
        },
    }


def _jobs_list_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id"],
        "properties": {
            "session_id": _session_id_prop(),
            "include_completed": {"type": "boolean", "description": "Include completed jobs."},
        },
    }


def _job_status_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", "job_id"],
        "properties": {
            "session_id": _session_id_prop(),
            "job_id": _job_id_prop(),
        },
    }


def _job_logs_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", "job_id"],
        "properties": {
            "session_id": _session_id_prop(),
            "job_id": _job_id_prop(),
            "tail_bytes": {"type": "integer", "description": "Tail bytes to read from the job logs."},
        },
    }


def _job_stop_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", "job_id"],
        "properties": {
            "session_id": _session_id_prop(),
            "job_id": _job_id_prop(),
            "force": {"type": "boolean", "description": "Force stop the job."},
        },
    }


MODULE = ModuleDefinition(
    name="runtime_exec",
    label="Runtime Exec",
    description=(
        "Execute arbitrary shell commands in a per-session runtime workspace. "
        "privilege=user runs in a confined sandbox limited to workspace writes. "
        "privilege=root runs unconfined and requires explicit approval."
    ),
    icon="terminal",
    pinned=True,
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="run",
            label="Run Command",
            description="Run a shell command inside the session runtime workspace.",
            streaming=True,
            handler=handle_run,
            approval=ApprovalDefinition(
                mode="conditional",
                evaluator=_runtime_exec_approval_evaluator,
            ),
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="jobs_list",
            label="List Jobs",
            description="List detached runtime jobs for a session.",
            handler=handle_jobs_list,
            parameters_schema=_jobs_list_parameters_schema(),
        ),
        ActionDefinition(
            id="job_status",
            label="Job Status",
            description="Read status for one detached runtime job.",
            handler=handle_job_status,
            parameters_schema=_job_status_parameters_schema(),
        ),
        ActionDefinition(
            id="job_logs",
            label="Job Logs",
            description="Read logs for one detached runtime job.",
            handler=handle_job_logs,
            parameters_schema=_job_logs_parameters_schema(),
        ),
        ActionDefinition(
            id="job_stop",
            label="Stop Job",
            description="Stop one detached runtime job.",
            handler=handle_job_stop,
            parameters_schema=_job_stop_parameters_schema(),
        ),
    ],
)
