from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import (
    handle_accounts,
    handle_run_read,
    handle_run_write,
)
def _run_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["cli_command"],
        "properties": {
            "cli_command": {
                "type": "string",
                "description": "Git or supported GitHub CLI command to execute.",
            },
            "cwd": {
                "type": "string",
                "description": "Optional working directory relative to the session workspace.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Execution timeout in seconds (default 600, max 3600).",
            },
            "git_account_name": {
                "type": "string",
                "description": "Optional explicit git account name to use.",
            },
            "git_account_id": {
                "type": "string",
                "description": "Optional explicit git account UUID to use.",
            },
        },
    }


def _accounts_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "host": {
                "type": "string",
                "description": "Optional host filter (for example: github.com).",
            },
            "repo_url": {
                "type": "string",
                "description": "Optional repository URL filter.",
            },
            "require_write": {
                "type": "boolean",
                "description": "If true, only accounts with a write token are returned.",
            },
        },
    }


MODULE = ModuleDefinition(
    name="git_exec",
    label="Git Exec",
    description=(
        "Execute git commands and selected GitHub CLI commands inside the session workspace with managed credentials. "
        "Allowed gh commands: `gh repo clone`, `gh repo list`, `gh repo view`, `gh pr view`, `gh pr create`, `gh pr merge`, `gh api` (GET/POST/PUT). "
        "Allowed network git reads include `git clone/fetch/pull/ls-remote/submodule/request-pull`; "
        "`git request-pull` only generates a pull-request summary and does not open a GitHub PR."
    ),
    icon="git-branch",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="run_read",
            label="Run Standard Git Command",
            description="Execute a git or supported gh read-oriented command inside the session workspace.",
            handler=handle_run_read,
            requires_runtime_context=True,
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="run_write",
            label="Run Write Git Command",
            description="Execute a git or supported gh write-oriented command inside the session workspace.",
            handler=handle_run_write,
            approval=True,
            requires_runtime_context=True,
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="accounts",
            label="List Git Accounts",
            description="List configured git accounts available to the agent.",
            handler=handle_accounts,
            parameters_schema=_accounts_parameters_schema(),
        ),
    ],
)
