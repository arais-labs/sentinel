from __future__ import annotations

from app.services.araios.module_types import ActionDefinition, ApprovalDefinition, ModuleDefinition

from .handlers import (
    ALLOWED_GIT_EXEC_OPERATIONS,
    _git_exec_approval_waiter,
    _git_exec_tool_approval_evaluator,
    handle_operation,
)


def _git_exec_parameters_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(ALLOWED_GIT_EXEC_OPERATIONS),
                "description": "Optional selector. Use 'run' (default) to execute git/gh commands or 'accounts' to list configured git accounts.",
            },
            "session_id": {
                "type": "string",
                "description": "Current session ID (required for operation=run, auto-injected by the agent loop).",
            },
            "command": {
                "type": "string",
                "description": "Git/GitHub CLI command for operation=run. Examples: 'git status', 'git fetch origin', 'git request-pull origin/main https://github.com/org/repo.git feature', 'gh repo list <org>', 'gh pr view 37 --json state,mergeStateStatus', 'gh pr create --repo <org>/<repo> ...', 'gh pr merge 37 --repo <org>/<repo> --merge', 'gh api -X PUT /repos/<org>/<repo>/pulls/<num>/merge'.",
            },
            "cwd": {
                "type": "string",
                "description": "Optional working directory relative to session workspace for operation=run.",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Execution timeout in seconds for operation=run (default 600, max 3600).",
            },
            "approval_timeout_seconds": {
                "type": "integer",
                "description": "Push approval wait timeout in seconds for operation=run (default 600, max 3600).",
            },
            "git_account_name": {
                "type": "string",
                "description": "Optional explicit git account name to use for operation=run.",
            },
            "git_account_id": {
                "type": "string",
                "description": "Optional explicit git account UUID to use for operation=run.",
            },
            "host": {
                "type": "string",
                "description": "Optional host filter for operation=accounts (for example: github.com).",
            },
            "repo_url": {
                "type": "string",
                "description": "Optional repository URL filter for operation=accounts.",
            },
            "require_write": {
                "type": "boolean",
                "description": "If true, operation=accounts only returns accounts with a write token.",
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
    actions=[
        ActionDefinition(
            id="run",
            label="Git Exec",
            description=(
                "Unified git tool entry point. Default operation runs git/gh commands; operation=accounts lists configured git accounts."
            ),
            handler=handle_operation,
            approval=ApprovalDefinition(
                mode="conditional",
                evaluator=_git_exec_tool_approval_evaluator,
                waiter=_git_exec_approval_waiter,
            ),
            parameters_schema=_git_exec_parameters_schema(),
        )
    ],
)
