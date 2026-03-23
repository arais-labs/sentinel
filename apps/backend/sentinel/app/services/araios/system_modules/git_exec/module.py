from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import (
    handle_accounts,
    handle_run_read,
    handle_run_write,
)


def _session_id_prop() -> dict:
    return {"type": "string", "description": "Current session ID."}


def _run_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["session_id", "cli_command"],
        "properties": {
            "session_id": _session_id_prop(),
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
        "Allowed gh commands: `gh repo list`, `gh repo view`, `gh pr view`, `gh pr create`, `gh pr merge`, `gh api` (GET/POST/PUT). "
        "Allowed network git reads include `git clone/fetch/pull/ls-remote/submodule/request-pull`; "
        "`git request-pull` only generates a pull-request summary and does not open a GitHub PR. "
        "To open or merge a PR on GitHub, use `gh pr create` or `gh pr merge` (approval-gated). "
        "Write operations (`git push`, `gh pr create`, `gh pr merge`, `gh api -X POST`, `gh api -X PUT`) require explicit approval before execution."
    ),
    icon="git-branch",
    pinned=True,
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="run_read",
            label="Run Standard Git Command",
            description="Execute a non-approval-gated git or supported gh command inside the session workspace.",
            handler=handle_run_read,
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="run_write",
            label="Run Write Git Command",
            description="Execute an approval-gated git or supported gh write command inside the session workspace.",
            handler=handle_run_write,
            approval=True,
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
