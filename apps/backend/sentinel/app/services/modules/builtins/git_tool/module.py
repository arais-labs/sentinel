from __future__ import annotations

from app.services.modules.definitions import ActionDefinition, ModuleDefinition

from .handlers import (
    handle_accounts,
    handle_gh_read,
    handle_gh_write,
    handle_read,
    handle_repos,
    handle_run_script,
    handle_write,
)


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
                    "Use action=read for git clone/fetch/status/log/diff; action=write for local changes, pull and push. Use gh_read for "
                    "gh repo clone/list/view, gh pr list/view/diff, and gh api GET. Use gh_write for "
                    "gh mutations like pr create/edit/close/merge, "
                    "and API POST/PATCH/PUT/DELETE (including implicit POST from fields)."
                ),
            },
            "cwd": {
                "type": "string",
                "description": "Working directory anywhere inside the workspace container. Relative paths start at the attached project root; absolute paths such as /tmp/repo work.",
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
        },
    }


def _script_parameters_schema() -> dict:
    properties = _run_parameters_schema()["properties"].copy()
    properties.pop("cli_command")
    properties["script"] = {
        "type": "string",
        "description": "Shell script executed by bash, for example bash scripts/pr/ready.sh --check or make pr-ready. Nested Git/GitHub commands receive brokered access. Required executables must already be installed.",
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["script"],
        "properties": properties,
    }


MODULE = ModuleDefinition(
    name="git",
    label="Git",
    description=(
        "Execute git and selected GitHub CLI commands inside the attached workspace container "
        "with externally brokered authentication. Prefer this tool; local terminal Git is also allowed. Use repos to discover accessible personal and organization repositories before asking the user for a URL."
    ),
    icon="git-branch",
    system=True,
    grouped_tool=True,
    actions=[
        ActionDefinition(
            id="run_script",
            label="Run Authenticated Script",
            description="Run a shell script with managed Git/GitHub authentication. Independently permits remote reads and writes within the selected account scope, regardless of other Git action permissions. Can change local files. Tokens stay outside the workspace. Use for scripts that internally invoke git or gh; prefer individual actions otherwise. Requires an origin remote or an explicit account.",
            handler=handle_run_script,
            approval=True,
            requires_runtime_context=True,
            parameters_schema=_script_parameters_schema(),
        ),
        ActionDefinition(
            id="repos",
            label="Find Accessible Repositories",
            description="Find repositories across connected accounts, including organization and collaborator access. Use before asking for an owner or URL.",
            handler=handle_repos,
            parameters_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional repository name or owner/name substring.",
                    }
                },
            },
        ),
        ActionDefinition(
            id="gh_read",
            label="Read GitHub Command",
            description="Execute one gh inspection command: repo/search, PR/issue list/view/diff/checks, or API GET. Fields imply POST unless GET is explicit.",
            handler=handle_gh_read,
            requires_runtime_context=True,
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="gh_write",
            label="Write GitHub Command",
            description="Execute one gh mutation: PR/issue create/edit/close/review/merge or API POST/PUT/PATCH/DELETE.",
            handler=handle_gh_write,
            approval=True,
            requires_runtime_context=True,
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="read",
            label="Read Git Command",
            description=(
                "Execute a read-oriented git command inside the session workspace. "
                "Use for clone, fetch, status, log, diff, and local inspection."
            ),
            handler=handle_read,
            requires_runtime_context=True,
            parameters_schema=_run_parameters_schema(),
        ),
        ActionDefinition(
            id="write",
            label="Write Git Command",
            description=(
                "Execute a mutating git command inside the session workspace. "
                "Use for staging, commit, pull, push, config changes, and branch/worktree changes."
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
