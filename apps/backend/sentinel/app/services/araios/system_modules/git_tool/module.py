from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ModuleDefinition

from .handlers import handle_accounts, handle_read, handle_write


def _run_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["cli_command"],
        "properties": {
            "cli_command": {
                "type": "string",
                "description": (
                    "Git or supported GitHub CLI command to execute in the session runtime workspace. "
                    "Use command=read for reads like git clone/fetch/pull/status/log/diff, "
                    "gh repo clone/list/view, gh pr view, and gh api GET. Use command=write for "
                    "mutations like git push/commit/add/reset/checkout/switch, gh pr create/merge, "
                    "and gh api POST/PUT."
                ),
            },
            "cwd": {
                "type": "string",
                "description": "Optional working directory inside /workspace.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Execution timeout in seconds. Defaults to 600, max 3600.",
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
                "description": "Optional host filter, for example github.com.",
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
    name="git",
    label="Git",
    description=(
        "Execute git and selected GitHub CLI commands inside the SSH runtime workspace "
        "with managed credentials. Prefer this over running git or gh through the shell."
    ),
    icon="git-branch",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="read",
            label="Read Git Command",
            description=(
                "Execute a read-oriented git or gh command inside the session workspace. "
                "Use for clone, fetch, pull, status, log, diff, repo listing/viewing, PR viewing, and gh api GET."
            ),
            handler=handle_read,
            requires_runtime_context=True,
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="write",
            label="Write Git Command",
            description=(
                "Execute a mutating git or gh command inside the session workspace. "
                "Use for push, commit, branch/worktree changes, gh pr create/merge, and gh api POST/PUT."
            ),
            handler=handle_write,
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
