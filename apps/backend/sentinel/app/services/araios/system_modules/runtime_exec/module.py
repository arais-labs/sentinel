from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ApprovalDefinition, ModuleDefinition

from .handlers import (
    ALLOWED_RUNTIME_EXEC_OPERATIONS,
    _runtime_exec_tool_approval_evaluator,
    handle_operation,
)


def _runtime_exec_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(ALLOWED_RUNTIME_EXEC_OPERATIONS),
                "description": "Optional selector. Use 'run' (default) for shell execution or one of the job operations for detached jobs.",
            },
            "session_id": {"type": "string", "description": "Current session ID (auto-injected for run)."},
            "command": {"type": "string", "description": "Shell command for operation=run."},
            "privilege": {
                "type": "string",
                "enum": ["user", "root"],
                "description": "Execution privilege mode for operation=run (default user). root requires approval.",
            },
            "cwd": {"type": "string", "description": "Working directory inside the session workspace for operation=run."},
            "env": {"type": "object", "description": "Environment variable overrides for operation=run."},
            "timeout_seconds": {"type": "integer", "description": "Execution timeout for operation=run (default 300, max 1800)."},
            "approval_timeout_seconds": {"type": "integer", "description": "Root approval wait timeout for operation=run (default 600, max 3600)."},
            "detached": {"type": "boolean", "description": "Run in background as a tracked job for operation=run."},
            "include_completed": {"type": "boolean", "description": "Include completed jobs for operation=jobs_list."},
            "job_id": {"type": "string", "description": "Detached job id for job_status, job_logs, or job_stop."},
            "tail_bytes": {"type": "integer", "description": "Tail bytes to read for operation=job_logs."},
            "force": {"type": "boolean", "description": "Force stop for operation=job_stop."},
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
    actions=[
        ActionDefinition(
            id="run",
            label="Runtime Exec",
            description="Unified runtime execution entry point. Default operation runs a shell command; other operations inspect or manage detached jobs.",
            streaming=True,
            handler=handle_operation,
            approval=ApprovalDefinition(
                mode="conditional",
                evaluator=_runtime_exec_tool_approval_evaluator,
            ),
            parameters_schema=_runtime_exec_parameters_schema(),
        )
    ],
)
