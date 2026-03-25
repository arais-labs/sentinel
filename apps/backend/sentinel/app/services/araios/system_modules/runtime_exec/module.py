from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import (
    handle_job_logs,
    handle_job_status,
    handle_job_stop,
    handle_jobs_list,
    handle_run_root,
    handle_run_user,
)
def _job_id_prop() -> dict:
    return {"type": "string", "description": "Detached job ID."}


def _run_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["shell_command"],
        "properties": {
            "shell_command": {"type": "string", "description": "Shell command to execute."},
            "cwd": {"type": "string", "description": "Working directory inside the session workspace."},
            "env": {"type": "object", "description": "Environment variable overrides."},
            "timeout_seconds": {"type": "integer", "description": "Execution timeout in seconds (default 300, max 1800)."},
            "detached": {"type": "boolean", "description": "Run in the background as a tracked job."},
        },
    }


def _jobs_list_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [],
        "properties": {
            "include_completed": {"type": "boolean", "description": "Include completed jobs."},
        },
    }


def _job_status_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["job_id"],
        "properties": {
            "job_id": _job_id_prop(),
        },
    }


def _job_logs_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["job_id"],
        "properties": {
            "job_id": _job_id_prop(),
            "tail_bytes": {"type": "integer", "description": "Tail bytes to read from the job logs."},
        },
    }


def _job_stop_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["job_id"],
        "properties": {
            "job_id": _job_id_prop(),
            "force": {"type": "boolean", "description": "Force stop the job."},
        },
    }


MODULE = ModuleDefinition(
    name="runtime_exec",
    label="Runtime Exec",
    description=(
        "Execute arbitrary shell commands in a per-session runtime workspace. "
        "User commands run in a confined sandbox limited to workspace writes. "
        "Root commands run unconfined."
    ),
    icon="terminal",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="run_user",
            label="Run User Command",
            description="Run a user-privileged shell command inside the session runtime workspace.",
            streaming=True,
            handler=handle_run_user,
            requires_runtime_context=True,
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="run_root",
            label="Run Root Command",
            description="Run a root-privileged shell command inside the session runtime workspace.",
            streaming=True,
            handler=handle_run_root,
            approval=True,
            requires_runtime_context=True,
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="jobs_list",
            label="List Jobs",
            description="List detached runtime jobs for a session.",
            handler=handle_jobs_list,
            requires_runtime_context=True,
            parameters_schema=_jobs_list_parameters_schema(),
        ),
        ActionDefinition(
            id="job_status",
            label="Job Status",
            description="Read status for one detached runtime job.",
            handler=handle_job_status,
            requires_runtime_context=True,
            parameters_schema=_job_status_parameters_schema(),
        ),
        ActionDefinition(
            id="job_logs",
            label="Job Logs",
            description="Read logs for one detached runtime job.",
            handler=handle_job_logs,
            requires_runtime_context=True,
            parameters_schema=_job_logs_parameters_schema(),
        ),
        ActionDefinition(
            id="job_stop",
            label="Stop Job",
            description="Stop one detached runtime job.",
            handler=handle_job_stop,
            requires_runtime_context=True,
            parameters_schema=_job_stop_parameters_schema(),
        ),
    ],
)
