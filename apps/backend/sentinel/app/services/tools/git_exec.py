from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import os
import shlex
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models import GitAccount, GitPushApproval, Session
from app.services.session_runtime import ensure_runtime_layout, runtime_workspace_dir
from app.services.tools.executor import ToolExecutionError, ToolValidationError
from app.services.tools.registry import (
    ToolApprovalEvaluation,
    ToolApprovalGate,
    ToolApprovalMode,
    ToolApprovalOutcome,
    ToolApprovalOutcomeStatus,
    ToolApprovalRequirement,
    ToolDefinition,
)

_MAX_GIT_OUTPUT_CHARS = 50_000
_FORBIDDEN_GIT_GLOBAL_FLAGS = {"-c", "-C", "--git-dir", "--work-tree"}
_NETWORK_READ_SUBCOMMANDS = {"clone", "fetch", "pull", "ls-remote", "submodule", "request-pull"}
_NETWORK_WRITE_SUBCOMMANDS = {"push"}
_GH_NETWORK_READ_SUBCOMMANDS = {
    ("repo", "list"),
    ("repo", "view"),
    ("pr", "view"),
    ("api", None),
}
_GH_NETWORK_WRITE_SUBCOMMANDS = {
    ("pr", "create"),
}
_GH_API_WRITE_METHODS = {"POST"}
_TERMINAL_WAIT_POLL_SECONDS = 1.5
_DEFAULT_GIT_TIMEOUT_SECONDS = 600
_MAX_GIT_TIMEOUT_SECONDS = 3600
_DEFAULT_PUSH_APPROVAL_TIMEOUT_SECONDS = 600
_MAX_PUSH_APPROVAL_TIMEOUT_SECONDS = 3600


@dataclass(frozen=True, slots=True)
class _RepoRef:
    host: str
    path: str
    target: str


@dataclass(frozen=True, slots=True)
class _ResolvedAccount:
    account: GitAccount
    repo: _RepoRef


def git_exec_tool(*, session_factory: async_sessionmaker[AsyncSession]) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        session_id_raw = payload.get("session_id")
        if not isinstance(session_id_raw, str) or not session_id_raw.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        try:
            session_id = UUID(session_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'session_id' must be a valid UUID string") from exc

        command = payload.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolValidationError("Field 'command' must be a non-empty string")

        timeout_seconds = payload.get("timeout_seconds", _DEFAULT_GIT_TIMEOUT_SECONDS)
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 1
        ):
            raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")
        timeout_seconds = min(timeout_seconds, _MAX_GIT_TIMEOUT_SECONDS)

        cwd_raw = payload.get("cwd")
        if cwd_raw is not None and (not isinstance(cwd_raw, str) or not cwd_raw.strip()):
            raise ToolValidationError("Field 'cwd' must be a non-empty string when provided")

        await _ensure_session_exists(session_factory, session_id)
        await ensure_runtime_layout(session_id)
        workspace_dir = runtime_workspace_dir(session_id)
        run_dir = _resolve_run_dir(workspace_dir, cwd_raw)

        tokens = _parse_cli_command(command.strip())
        if tokens[0] == "gh":
            return await _execute_gh_command(
                session_factory=session_factory,
                session_id=session_id,
                workspace_dir=workspace_dir,
                run_dir=run_dir,
                tokens=tokens,
                timeout_seconds=timeout_seconds,
                approval=_approval_context(payload),
            )

        subcommand, subcommand_index = _extract_git_subcommand(tokens)
        subargs = tokens[subcommand_index + 1 :]

        _validate_no_forbidden_global_flags(tokens[:subcommand_index])
        _validate_no_forbidden_global_flags(subargs)

        network_mode = _network_mode_for_command(subcommand)
        account: _ResolvedAccount | None = None

        if network_mode is not None:
            repo_url, remote_name = _resolve_network_repo_url(subcommand, subargs, run_dir)
            async with session_factory() as db:
                account = await _resolve_git_account(db, repo_url=repo_url, require_write=network_mode == "write")
            if account is None:
                repo_target = _repo_target_label(repo_url)
                required_token = "write token" if network_mode == "write" else "read token"
                raise ToolValidationError(
                    "No matching git account is configured for "
                    f"'{repo_target}' ({network_mode} access). "
                    f"Add/update a Git account with matching host/scope and a {required_token}."
                )

            result = await _run_network_git(
                account=account,
                workspace_dir=workspace_dir,
                run_dir=run_dir,
                tokens=tokens,
                timeout_seconds=timeout_seconds,
            )
            approval = _approval_context(payload)
            if approval is not None and approval.get("provider") == "git":
                await _record_push_result(
                    session_factory=session_factory,
                    approval_id=UUID(str(approval["approval_id"])),
                    result=result,
                )
            if subcommand == "clone" and account is not None and result["ok"]:
                await _configure_author_identity_after_clone(
                    run_dir=run_dir,
                    subargs=subargs,
                    repo_url=repo_url,
                    author_name=account.account.author_name,
                    author_email=account.account.author_email,
                    timeout_seconds=min(60, timeout_seconds),
                )
            return result

        if subcommand == "commit":
            origin_url = _resolve_origin_url(run_dir)
            async with session_factory() as db:
                account = await _resolve_git_account(db, repo_url=origin_url, require_write=False)
            if account is None:
                repo_target = _repo_target_label(origin_url)
                raise ToolValidationError(
                    "No matching git account is configured for "
                    f"'{repo_target}' (commit attribution). "
                    "Add/update a Git account with matching host/scope and a read token."
                )
            result = await _run_local_git(
                run_dir=run_dir,
                tokens=tokens,
                timeout_seconds=timeout_seconds,
                author_name=account.account.author_name,
                author_email=account.account.author_email,
            )
            result["author"] = {
                "name": account.account.author_name,
                "email": account.account.author_email,
            }
            return result

        return await _run_local_git(
            run_dir=run_dir,
            tokens=tokens,
            timeout_seconds=timeout_seconds,
            author_name=None,
            author_email=None,
        )

    return ToolDefinition(
        name="git_exec",
        description=(
            "Execute git commands and selected GitHub CLI commands inside the session workspace with managed credentials. "
            "Allowed gh commands: `gh repo list`, `gh repo view`, `gh pr view`, `gh pr create`, `gh api` (GET/POST). "
            "Allowed network git reads include `git clone/fetch/pull/ls-remote/submodule/request-pull`; "
            "`git request-pull` only generates a pull-request summary and does not open a GitHub PR. "
            "To open a PR on GitHub, use `gh pr create` (approval-gated). "
            "Write operations (`git push`, `gh pr create`, `gh api -X POST`) require explicit approval before execution."
        ),
        risk_level="high",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["command"],
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Current session ID (auto-injected by agent loop)",
                },
                "command": {
                    "type": "string",
                    "description": "Git/GitHub CLI command. Examples: 'git status', 'git fetch origin', 'git request-pull origin/main https://github.com/org/repo.git feature', 'gh repo list <org>', 'gh pr view 37 --json state,mergeStateStatus', 'gh pr create --repo <org>/<repo> ...', 'gh api -X POST /repos/<org>/<repo>/pulls'",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory relative to session workspace",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Execution timeout in seconds (default 600, max 3600)",
                },
                "approval_timeout_seconds": {
                    "type": "integer",
                    "description": "Push approval wait timeout in seconds (default 600, max 3600)",
                },
            },
        },
        execute=_execute,
        approval_gate=ToolApprovalGate(
            mode=ToolApprovalMode.CONDITIONAL,
            evaluator=_git_exec_approval_evaluator,
            waiter=_git_exec_approval_waiter(session_factory=session_factory),
        ),
    )


def _approval_context(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("__approval_gate")
    if isinstance(raw, dict):
        return raw
    return None


def _approval_timeout_from_payload(payload: dict[str, Any]) -> int:
    approval_timeout_seconds = payload.get(
        "approval_timeout_seconds",
        getattr(settings, "git_push_approval_timeout_seconds", _DEFAULT_PUSH_APPROVAL_TIMEOUT_SECONDS),
    )
    if (
        not isinstance(approval_timeout_seconds, int)
        or isinstance(approval_timeout_seconds, bool)
        or approval_timeout_seconds < 1
    ):
        raise ToolValidationError("Field 'approval_timeout_seconds' must be a positive integer")
    return min(approval_timeout_seconds, _MAX_PUSH_APPROVAL_TIMEOUT_SECONDS)


def _git_exec_approval_evaluator(payload: dict[str, Any]) -> ToolApprovalEvaluation:
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        return ToolApprovalEvaluation.allow()
    tokens = _parse_cli_command(command.strip())
    action = _approval_action_for_tokens(tokens)
    if action is None:
        return ToolApprovalEvaluation.allow()
    session_id = payload.get("session_id")
    requested_by = (
        f"session:{session_id.strip()}"
        if isinstance(session_id, str) and session_id.strip()
        else None
    )
    requirement = ToolApprovalRequirement(
        action=action,
        description=f"Allow write operation: {command.strip()}",
        timeout_seconds=_approval_timeout_from_payload(payload),
        match_key=_normalize_command(command),
        metadata={"tool_name": "git_exec", "command": command.strip()},
        requested_by=requested_by,
    )
    return ToolApprovalEvaluation.require(requirement)


def _git_exec_approval_waiter(
    *,
    session_factory: async_sessionmaker[AsyncSession],
):
    async def _waiter(
        tool_name: str,
        payload: dict[str, Any],
        requirement: ToolApprovalRequirement,
    ) -> ToolApprovalOutcome:
        _ = tool_name
        session_id_raw = payload.get("session_id")
        if not isinstance(session_id_raw, str) or not session_id_raw.strip():
            raise ToolValidationError("Field 'session_id' must be a non-empty string")
        try:
            session_id = UUID(session_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'session_id' must be a valid UUID string") from exc

        command = payload.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolValidationError("Field 'command' must be a non-empty string")
        tokens = _parse_cli_command(command.strip())

        await _ensure_session_exists(session_factory, session_id)
        await ensure_runtime_layout(session_id)
        workspace_dir = runtime_workspace_dir(session_id)
        run_dir = _resolve_run_dir(workspace_dir, payload.get("cwd"))

        if tokens[0] == "gh":
            host = _extract_gh_host(tokens)
            owner = _extract_gh_owner(tokens, run_dir=run_dir)
            if not owner:
                raise ToolValidationError(
                    "Unable to infer GitHub owner for approval-gated gh command."
                )
            repo_url = f"https://{host}/{owner}/_gh_scope"
            remote_name = "gh"
        else:
            subcommand, subcommand_index = _extract_git_subcommand(tokens)
            subargs = tokens[subcommand_index + 1 :]
            repo_url, remote_name = _resolve_network_repo_url(subcommand, subargs, run_dir)

        async with session_factory() as db:
            account = await _resolve_git_account(db, repo_url=repo_url, require_write=True)
        if account is None:
            repo_target = _repo_target_label(repo_url)
            raise ToolValidationError(
                "No matching git account is configured for "
                f"'{repo_target}' (write access). Add/update a Git account with a write token."
            )

        approval_row = await _create_push_approval(
            session_factory=session_factory,
            account_id=account.account.id,
            session_id=session_id,
            repo_url=repo_url,
            remote_name=remote_name,
            command=command.strip(),
            requested_by=requirement.requested_by or f"session:{session_id}",
            timeout_seconds=requirement.timeout_seconds,
        )
        try:
            decision = await _wait_for_push_approval(
                session_factory=session_factory,
                approval_id=approval_row.id,
                timeout_seconds=requirement.timeout_seconds,
            )
        except asyncio.CancelledError:
            await _cancel_push_approval(
                session_factory=session_factory,
                approval_id=approval_row.id,
                note="Cancelled by user while awaiting approval",
            )
            return ToolApprovalOutcome(
                status=ToolApprovalOutcomeStatus.CANCELLED,
                approval={
                    "provider": "git",
                    "approval_id": str(approval_row.id),
                    "status": "cancelled",
                    "pending": False,
                    "can_resolve": False,
                    "label": "Git write approval",
                    "match_key": _normalize_command(command),
                },
                message="Approval cancelled.",
            )

        return ToolApprovalOutcome(
            status=ToolApprovalOutcomeStatus(decision.status),
            approval={
                "provider": "git",
                "approval_id": str(approval_row.id),
                "status": decision.status,
                "pending": False,
                "can_resolve": False,
                "label": "Git write approval",
                "match_key": _normalize_command(command),
                "decision_by": decision.decision_by,
                "decision_note": decision.decision_note,
            },
            message=_approval_status_message(decision.status, decision.decision_note),
        )

    return _waiter


def _approval_action_for_tokens(tokens: list[str]) -> str | None:
    if not tokens:
        return None
    if tokens[0] == "git":
        subcommand, _ = _extract_git_subcommand(tokens)
        mode = _network_mode_for_command(subcommand)
        if mode == "write":
            return f"git.{subcommand}"
        return None
    if tokens[0] == "gh":
        mode = _gh_network_mode(tokens)
        if mode != "write":
            return None
        primary, secondary = _gh_subcommand(tokens)
        if primary == "api":
            method = _gh_api_method(tokens).lower()
            return f"gh.api.{method}"
        if secondary:
            return f"gh.{primary}.{secondary}"
        return f"gh.{primary}"
    return None


def _approval_status_message(status: str, note: str | None) -> str:
    detail = (note or "").strip()
    if status == "approved":
        return f"Approval approved: {detail}" if detail else "Approval approved."
    if status == "rejected":
        return f"Approval rejected: {detail}" if detail else "Approval rejected."
    if status == "timed_out":
        return f"Approval timed out: {detail}" if detail else "Approval timed out."
    if status == "cancelled":
        return f"Approval cancelled: {detail}" if detail else "Approval cancelled."
    return f"Approval {status}: {detail}" if detail else f"Approval {status}."


def _normalize_command(command: str) -> str:
    return " ".join(command.strip().split()).lower()


def _parse_cli_command(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ToolValidationError(f"Invalid command syntax: {exc}") from exc
    if not tokens:
        raise ToolValidationError("Command is empty")
    if tokens[0] not in {"git", "gh"}:
        raise ToolValidationError(
            "Only git or selected gh commands are allowed. "
            "Supported gh commands in git_exec: `gh repo list`, `gh repo view`, `gh pr view`, `gh pr create`, `gh api` (GET/POST)."
        )
    return tokens


async def _execute_gh_command(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    session_id: UUID,
    workspace_dir: Path,
    run_dir: Path,
    tokens: list[str],
    timeout_seconds: int,
    approval: dict[str, Any] | None,
) -> dict[str, Any]:
    host = _extract_gh_host(tokens)
    primary, secondary = _gh_subcommand(tokens)
    mode = _gh_network_mode(tokens)
    if mode not in {"read", "write"}:
        if primary == "auth":
            raise ToolValidationError(
                "Unsupported gh auth command in git_exec. "
                "Authentication is managed automatically via configured Git account tokens, "
                "so interactive auth commands like `gh auth status/login` are not needed. "
                "Use supported commands: `gh repo list <org>`, `gh repo view <org>/<repo>`, "
                "`gh pr view`, `gh pr create`, `gh api <endpoint>` (GET/POST)."
            )
        raise ToolValidationError(
            "Unsupported gh command in git_exec. "
            "Supported: `gh repo list <org>`, `gh repo view <org>/<repo>`, "
            "`gh pr view`, `gh pr create`, `gh api <endpoint>` (GET/POST)."
        )

    owner = _extract_gh_owner(tokens, run_dir=run_dir)
    if not owner:
        raise ToolValidationError(
            "Unable to infer GitHub owner for gh command. "
            "Provide explicit owner (for example: `gh repo list <org>`, `gh repo view <org>/<repo>`, "
            "or `gh api /orgs/<org>/repos`)."
        )

    scope_repo_url = f"https://{host}/{owner}/_gh_scope"
    async with session_factory() as db:
        account = await _resolve_git_account(
            db,
            repo_url=scope_repo_url,
            require_write=mode == "write",
        )
    if account is None:
        required_token = "write token" if mode == "write" else "read token"
        raise ToolValidationError(
            "No matching git account is configured for "
            f"'{host}/{owner}' ({mode} access). "
            f"Add/update a Git account with matching host/scope and a {required_token}."
        )

    token = (
        (account.account.token_write if mode == "write" else account.account.token_read)
        or ""
    ).strip()
    if not token:
        missing_kind = "write token" if mode == "write" else "read token"
        raise ToolValidationError(f"Matching git account is missing required {missing_kind}")

    env = os.environ.copy()
    env["HOME"] = str(workspace_dir)
    env["PWD"] = str(run_dir)
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    env["GH_HOST"] = host
    env["GH_PROMPT_DISABLED"] = "1"

    result = await _run_git_subprocess(
        args=tokens,
        run_dir=run_dir,
        env=env,
        timeout_seconds=timeout_seconds,
        redactions=[token],
    )
    result["network_mode"] = mode
    result["account"] = {
        "id": str(account.account.id),
        "name": account.account.name,
        "host": account.account.host,
        "scope_pattern": account.account.scope_pattern,
    }
    if approval is not None and approval.get("provider") == "git":
        await _record_push_result(
            session_factory=session_factory,
            approval_id=UUID(str(approval["approval_id"])),
            result=result,
        )
    return result


def _extract_gh_host(tokens: list[str]) -> str:
    idx = 1
    while idx < len(tokens):
        part = tokens[idx]
        if part == "--hostname":
            if idx + 1 < len(tokens) and tokens[idx + 1].strip():
                return tokens[idx + 1].strip().lower()
            raise ToolValidationError("gh --hostname requires a value")
        if part.startswith("--hostname="):
            value = part.split("=", 1)[1].strip().lower()
            if value:
                return value
            raise ToolValidationError("gh --hostname requires a value")
        idx += 1
    return "github.com"


def _gh_network_mode(tokens: list[str]) -> str | None:
    primary, secondary = _gh_subcommand(tokens)
    if (primary, secondary) in _GH_NETWORK_WRITE_SUBCOMMANDS:
        return "write"
    if (primary, secondary) in _GH_NETWORK_READ_SUBCOMMANDS:
        if primary == "api":
            method = _gh_api_method(tokens)
            if method == "GET":
                return "read"
            if method in _GH_API_WRITE_METHODS:
                return "write"
            raise ToolValidationError("gh api supports GET and POST in git_exec")
        return "read"
    return None


def _gh_subcommand(tokens: list[str]) -> tuple[str, str | None]:
    if len(tokens) < 2:
        raise ToolValidationError("Missing gh subcommand")
    primary = tokens[1].strip().lower()
    if primary.startswith("-"):
        raise ToolValidationError(
            "gh global flags before subcommand are not supported in git_exec; place subcommand first"
        )
    if primary == "api":
        return primary, None
    secondary: str | None = None
    if len(tokens) >= 3 and not tokens[2].startswith("-"):
        secondary = tokens[2].strip().lower()
    return primary, secondary


def _gh_api_method(tokens: list[str]) -> str:
    idx = 0
    method = "GET"
    while idx < len(tokens):
        part = tokens[idx]
        if part in {"-X", "--method"}:
            if idx + 1 >= len(tokens):
                raise ToolValidationError("gh api method flag requires a value")
            method = str(tokens[idx + 1]).strip().upper()
            idx += 2
            continue
        if part.startswith("--method="):
            method = part.split("=", 1)[1].strip().upper()
        idx += 1
    return method or "GET"


def _extract_gh_owner(tokens: list[str], *, run_dir: Path) -> str | None:
    primary, secondary = _gh_subcommand(tokens)
    if primary == "repo" and secondary == "list":
        owner = _first_positional_argument(tokens[3:])
        return _normalize_owner(owner)
    if primary == "repo" and secondary == "view":
        slug = _first_positional_argument(tokens[3:])
        if slug and "/" in slug:
            return _normalize_owner(slug.split("/", 1)[0])
        explicit_repo = _extract_option_value(tokens[3:], {"-R", "--repo"})
        if explicit_repo and "/" in explicit_repo:
            return _normalize_owner(explicit_repo.split("/", 1)[0])
        origin_url = _resolve_origin_url(run_dir)
        repo = _parse_repo_ref(origin_url)
        return _normalize_owner(repo.path.split("/", 1)[0])
    if primary == "api":
        endpoint = _first_positional_argument(tokens[2:])
        if endpoint:
            return _normalize_owner(_extract_owner_from_gh_api_endpoint(endpoint))
    if primary == "pr" and secondary == "create":
        explicit_repo = _extract_option_value(tokens[3:], {"-R", "--repo"})
        if explicit_repo and "/" in explicit_repo:
            return _normalize_owner(explicit_repo.split("/", 1)[0])
        origin_url = _resolve_origin_url(run_dir)
        repo = _parse_repo_ref(origin_url)
        return _normalize_owner(repo.path.split("/", 1)[0])
    if primary == "pr" and secondary == "view":
        explicit_repo = _extract_option_value(tokens[3:], {"-R", "--repo"})
        if explicit_repo and "/" in explicit_repo:
            return _normalize_owner(explicit_repo.split("/", 1)[0])
        origin_url = _resolve_origin_url(run_dir)
        repo = _parse_repo_ref(origin_url)
        return _normalize_owner(repo.path.split("/", 1)[0])
    return None


def _extract_owner_from_gh_api_endpoint(endpoint: str) -> str | None:
    value = endpoint.strip()
    if not value:
        return None
    if "://" in value:
        parsed = urlparse(value)
        value = parsed.path or ""
    path = value.strip().lstrip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "orgs" and parts[2] == "repos":
        return parts[1]
    if len(parts) >= 3 and parts[0] == "users" and parts[2] == "repos":
        return parts[1]
    if len(parts) >= 3 and parts[0] == "repos":
        return parts[1]
    return None


def _extract_option_value(args: list[str], option_names: set[str]) -> str | None:
    idx = 0
    while idx < len(args):
        part = args[idx]
        if part in option_names:
            if idx + 1 < len(args):
                value = args[idx + 1].strip()
                return value or None
            return None
        for name in option_names:
            prefix = f"{name}="
            if part.startswith(prefix):
                value = part.split("=", 1)[1].strip()
                return value or None
        idx += 1
    return None


def _normalize_owner(value: str | None) -> str | None:
    normalized = (value or "").strip().strip("/")
    return normalized.lower() if normalized else None


def _extract_git_subcommand(tokens: list[str]) -> tuple[str, int]:
    idx = 1
    while idx < len(tokens):
        part = tokens[idx]
        if part in {"-C", "-c", "--exec-path"}:
            idx += 2
            continue
        if part.startswith("--git-dir") or part.startswith("--work-tree"):
            idx += 1
            continue
        if part.startswith("-"):
            idx += 1
            continue
        return part, idx
    raise ToolValidationError("Missing git subcommand")


def _validate_no_forbidden_global_flags(tokens: list[str]) -> None:
    for token in tokens:
        if token in _FORBIDDEN_GIT_GLOBAL_FLAGS:
            raise ToolValidationError(f"Git flag '{token}' is not allowed in git_exec")
        if token.startswith("--git-dir=") or token.startswith("--work-tree="):
            raise ToolValidationError("Custom git-dir/work-tree is not allowed in git_exec")


def _resolve_run_dir(workspace_dir: Path, cwd_raw: Any) -> Path:
    run_dir = workspace_dir
    if isinstance(cwd_raw, str) and cwd_raw.strip():
        requested = cwd_raw.strip()
        candidate = (
            (workspace_dir / requested).resolve()
            if not Path(requested).is_absolute()
            else Path(requested).expanduser().resolve()
        )
        if candidate != workspace_dir and workspace_dir not in candidate.parents:
            raise ToolValidationError("Field 'cwd' must stay within session workspace")
        run_dir = candidate
        run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _network_mode_for_command(subcommand: str) -> str | None:
    if subcommand in _NETWORK_READ_SUBCOMMANDS:
        return "read"
    if subcommand in _NETWORK_WRITE_SUBCOMMANDS:
        return "write"
    return None


def _resolve_network_repo_url(subcommand: str, subargs: list[str], run_dir: Path) -> tuple[str, str]:
    if subcommand == "clone":
        repo_url = _extract_clone_repo_url(subargs)
        remote_name = _extract_clone_remote_name(subargs) or "origin"
        return repo_url, remote_name

    if subcommand == "ls-remote":
        candidate = _first_positional_argument(subargs)
        if candidate and _looks_like_repo_url(candidate):
            return candidate, "origin"

    if subcommand == "request-pull":
        repo_url, remote_name = _extract_request_pull_repo_url(subargs, run_dir)
        return repo_url, remote_name

    remote_name = _first_positional_argument(subargs) or "origin"
    repo_url = _resolve_origin_url(run_dir, remote_name=remote_name)
    return repo_url, remote_name


def _extract_request_pull_repo_url(subargs: list[str], run_dir: Path) -> tuple[str, str]:
    positionals = _positional_arguments(subargs)
    if len(positionals) < 2:
        raise ToolValidationError("git request-pull requires <start> and <url> arguments")
    upstream = positionals[1]
    if _looks_like_repo_url(upstream):
        return upstream, "origin"
    return _resolve_origin_url(run_dir, remote_name=upstream), upstream


def _extract_clone_repo_url(subargs: list[str]) -> str:
    repo_url = _first_positional_argument(subargs)
    if repo_url is None:
        raise ToolValidationError("git clone requires a repository URL")
    if not _looks_like_repo_url(repo_url):
        raise ToolValidationError("git clone repository argument must be a URL or ssh repo spec")
    return repo_url


def _extract_clone_remote_name(subargs: list[str]) -> str | None:
    idx = 0
    while idx < len(subargs):
        part = subargs[idx]
        if part in {"-o", "--origin"}:
            if idx + 1 < len(subargs):
                return subargs[idx + 1].strip()
            return None
        if part.startswith("--origin="):
            return part.split("=", 1)[1].strip()
        idx += 1
    return None


def _first_positional_argument(args: list[str]) -> str | None:
    positionals = _positional_arguments(args)
    return positionals[0] if positionals else None


def _positional_arguments(args: list[str]) -> list[str]:
    options_with_value = {
        "-b",
        "--branch",
        "-o",
        "--origin",
        "--depth",
        "--shallow-since",
        "--recurse-submodules",
        "--jobs",
        "--config",
        "--upload-pack",
        "--limit",
        "-L",
        "--json",
        "--jq",
        "--template",
        "--hostname",
        "-R",
        "--repo",
        "-X",
        "--method",
        "-f",
        "-F",
        "--field",
        "--raw-field",
        "-H",
        "--header",
    }
    positionals: list[str] = []
    idx = 0
    while idx < len(args):
        part = args[idx]
        if part == "--":
            positionals.extend(args[idx + 1 :])
            break
        if part in options_with_value:
            idx += 2
            continue
        if part.startswith("--") and "=" in part:
            idx += 1
            continue
        if part.startswith("-"):
            idx += 1
            continue
        positionals.append(part)
        idx += 1
    return positionals


def _looks_like_repo_url(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    if "://" in candidate:
        parsed = urlparse(candidate)
        return parsed.scheme in {"http", "https", "ssh"} and bool(parsed.hostname)
    if candidate.startswith("git@") and ":" in candidate:
        return True
    return False


def _resolve_origin_url(run_dir: Path, remote_name: str = "origin") -> str:
    args = ["git", "remote", "get-url", remote_name]
    result = _run_blocking(args=args, cwd=run_dir, env=os.environ.copy(), timeout_seconds=30)
    if result["returncode"] != 0:
        raise ToolValidationError(_format_remote_resolution_error(result=result, remote_name=remote_name))
    repo_url = (result["stdout"] or "").strip()
    if not repo_url:
        raise ToolValidationError("Repository remote URL is empty")
    return repo_url


def _format_remote_resolution_error(*, result: dict[str, Any], remote_name: str) -> str:
    stderr = (result.get("stderr") or "").strip()
    lowered = stderr.lower()
    if "not a git repository" in lowered:
        return (
            "git fetch/pull requires a git repository in the selected cwd. "
            "Run `git clone <repo>` first or set `cwd` to an existing repository in the session workspace."
        )
    if "no such remote" in lowered or "could not get url" in lowered:
        return (
            f"Git remote '{remote_name}' was not found in this repository. "
            "Run `git remote -v` and use a valid remote name."
        )
    detail = stderr.splitlines()[0] if stderr else ""
    suffix = f" (git: {detail})" if detail else ""
    return (
        "Unable to resolve repository remote URL for account matching. "
        "Ensure the repository has a configured remote URL."
        f"{suffix}"
    )


def _run_blocking(
    *,
    args: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> dict[str, Any]:
    import subprocess

    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "returncode": int(completed.returncode),
            "stdout": _truncate_output(completed.stdout),
            "stderr": _truncate_output(completed.stderr),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": -1,
            "stdout": _truncate_output((exc.stdout or "") if isinstance(exc.stdout, str) else ""),
            "stderr": _truncate_output((exc.stderr or "") if isinstance(exc.stderr, str) else ""),
            "timed_out": True,
        }


async def _run_local_git(
    *,
    run_dir: Path,
    tokens: list[str],
    timeout_seconds: int,
    author_name: str | None,
    author_email: str | None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["HOME"] = str(run_dir)
    env["PWD"] = str(run_dir)
    if author_name and author_email:
        env["GIT_AUTHOR_NAME"] = author_name
        env["GIT_AUTHOR_EMAIL"] = author_email
        env["GIT_COMMITTER_NAME"] = author_name
        env["GIT_COMMITTER_EMAIL"] = author_email
    return await _run_git_subprocess(args=tokens, run_dir=run_dir, env=env, timeout_seconds=timeout_seconds)


async def _run_network_git(
    *,
    account: _ResolvedAccount | None,
    workspace_dir: Path,
    run_dir: Path,
    tokens: list[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    if account is None:
        raise ToolValidationError("No matching git account found for this repository")
    subcommand, subcommand_index = _extract_git_subcommand(tokens)
    mode = _network_mode_for_command(subcommand)
    if mode is None:
        raise ToolValidationError("Expected network git command")
    token = account.account.token_write if mode == "write" else account.account.token_read
    if not token.strip():
        raise ToolValidationError("Matching git account is missing required token")

    env = os.environ.copy()
    env["HOME"] = str(workspace_dir)
    env["PWD"] = str(run_dir)
    askpass_file = _create_askpass_script(token=token)
    try:
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = str(askpass_file)
        env["SSH_ASKPASS"] = str(askpass_file)
        env["GIT_EXEC_USERNAME"] = "x-access-token"
        env["GIT_EXEC_PASSWORD"] = token

        args = tokens[:subcommand_index] + ["-c", "credential.helper="] + tokens[subcommand_index:]
        result = await _run_git_subprocess(
            args=args,
            run_dir=run_dir,
            env=env,
            timeout_seconds=timeout_seconds,
            redactions=[token],
        )
        result["network_mode"] = mode
        result["account"] = {
            "id": str(account.account.id),
            "name": account.account.name,
            "host": account.account.host,
            "scope_pattern": account.account.scope_pattern,
        }
        return result
    finally:
        with contextlib.suppress(OSError):
            askpass_file.unlink()


def _create_askpass_script(*, token: str) -> Path:
    with tempfile.NamedTemporaryFile("w", delete=False, prefix="sentinel-git-askpass-", suffix=".sh") as handle:
        handle.write("#!/bin/sh\n")
        handle.write('prompt="$1"\n')
        handle.write('case "$prompt" in\n')
        handle.write('  *Username* ) echo "${GIT_EXEC_USERNAME:-x-access-token}" ;;\n')
        handle.write('  * ) echo "${GIT_EXEC_PASSWORD:-}" ;;\n')
        handle.write("esac\n")
        path = Path(handle.name)
    path.chmod(stat.S_IRWXU)
    _ = token
    return path


async def _run_git_subprocess(
    *,
    args: list[str],
    run_dir: Path,
    env: dict[str, str],
    timeout_seconds: int,
    redactions: list[str] | None = None,
) -> dict[str, Any]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(run_dir),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        timed_out = False
    except TimeoutError:
        timed_out = True
        if proc.returncode is None:
            proc.kill()
        stdout_bytes, stderr_bytes = await proc.communicate()

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    if redactions:
        for secret in redactions:
            if secret:
                stdout = stdout.replace(secret, "***")
                stderr = stderr.replace(secret, "***")
    return {
        "ok": not timed_out and proc.returncode == 0,
        "returncode": int(proc.returncode if proc.returncode is not None else -1),
        "timed_out": timed_out,
        "stdout": _truncate_output(stdout),
        "stderr": _truncate_output(stderr),
        "cwd": str(run_dir),
        "command": " ".join(args),
    }


def _truncate_output(value: str | None) -> str:
    text = value or ""
    if len(text) <= _MAX_GIT_OUTPUT_CHARS:
        return text
    return f"{text[:_MAX_GIT_OUTPUT_CHARS]}\n...[truncated]"


async def _ensure_session_exists(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: UUID,
) -> None:
    async with session_factory() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalars().first()
        if session is None:
            raise ToolValidationError("Session not found")


def _parse_repo_ref(repo_url: str) -> _RepoRef:
    raw = repo_url.strip()
    if not raw:
        raise ToolValidationError("Repository URL is empty")

    host = ""
    path = ""
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").strip().lower()
        path = parsed.path.strip().lstrip("/")
    elif raw.startswith("git@") and ":" in raw:
        host = raw[4:].split(":", 1)[0].strip().lower()
        path = raw.split(":", 1)[1].strip().lstrip("/")
    else:
        raise ToolValidationError("Unsupported repository URL format for account matching")

    if path.endswith(".git"):
        path = path[:-4]
    path = path.strip("/")
    if not host or not path:
        raise ToolValidationError("Invalid repository URL for account matching")
    return _RepoRef(host=host, path=path.lower(), target=f"{host}/{path.lower()}")


def _repo_target_label(repo_url: str) -> str:
    try:
        return _parse_repo_ref(repo_url).target
    except ToolValidationError:
        return repo_url.strip() or "<unknown-repository>"


def _specificity(pattern: str) -> int:
    return sum(1 for ch in pattern if ch not in {"*", "?", "["})


async def _resolve_git_account(
    db: AsyncSession,
    *,
    repo_url: str,
    require_write: bool,
) -> _ResolvedAccount | None:
    repo = _parse_repo_ref(repo_url)
    result = await db.execute(select(GitAccount))
    accounts = result.scalars().all()

    best: tuple[int, GitAccount] | None = None
    for item in accounts:
        host = (item.host or "").strip().lower()
        if host != repo.host:
            continue
        pattern_raw = (item.scope_pattern or "*").strip().lower() or "*"
        matches = fnmatch.fnmatch(repo.target, pattern_raw) or fnmatch.fnmatch(repo.path, pattern_raw)
        if not matches:
            continue
        if not (item.token_write.strip() if require_write else item.token_read.strip()):
            continue
        score = _specificity(pattern_raw)
        if best is None or score > best[0]:
            best = (score, item)

    if best is None:
        return None
    return _ResolvedAccount(account=best[1], repo=repo)


@dataclass(frozen=True, slots=True)
class _ApprovalDecision:
    status: str
    decision_by: str | None
    decision_note: str | None


async def _create_push_approval(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    account_id: UUID,
    session_id: UUID,
    repo_url: str,
    remote_name: str,
    command: str,
    requested_by: str,
    timeout_seconds: int,
) -> GitPushApproval:
    async with session_factory() as db:
        row = GitPushApproval(
            account_id=account_id,
            session_id=session_id,
            repo_url=repo_url,
            remote_name=remote_name,
            command=command,
            status="pending",
            requested_by=requested_by,
            expires_at=datetime.now(UTC) + timedelta(seconds=timeout_seconds),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def _wait_for_push_approval(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    approval_id: UUID,
    timeout_seconds: int,
) -> _ApprovalDecision:
    started_at = datetime.now(UTC)
    while True:
        async with session_factory() as db:
            result = await db.execute(select(GitPushApproval).where(GitPushApproval.id == approval_id))
            row = result.scalars().first()
            if row is None:
                raise ToolExecutionError("Push approval record disappeared")
            if row.status in {"approved", "rejected", "cancelled", "timed_out"}:
                return _ApprovalDecision(
                    status=row.status,
                    decision_by=row.decision_by,
                    decision_note=row.decision_note,
                )
            now = datetime.now(UTC)
            expired_by_row = row.expires_at <= now
            expired_by_wait = (now - started_at).total_seconds() >= timeout_seconds
            if expired_by_row or expired_by_wait:
                row.status = "timed_out"
                row.resolved_at = now
                await db.commit()
                return _ApprovalDecision(status="timed_out", decision_by=None, decision_note=None)
        await asyncio.sleep(_TERMINAL_WAIT_POLL_SECONDS)


async def _record_push_result(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    approval_id: UUID,
    result: dict[str, Any],
) -> None:
    async with session_factory() as db:
        db_result = await db.execute(select(GitPushApproval).where(GitPushApproval.id == approval_id))
        approval = db_result.scalars().first()
        if approval is None:
            return
        approval.result_json = result
        await db.commit()


async def _cancel_push_approval(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    approval_id: UUID,
    note: str,
) -> None:
    async with session_factory() as db:
        db_result = await db.execute(select(GitPushApproval).where(GitPushApproval.id == approval_id))
        approval = db_result.scalars().first()
        if approval is None:
            return
        if approval.status != "pending":
            return
        approval.status = "cancelled"
        approval.decision_note = note
        approval.resolved_at = datetime.now(UTC)
        await db.commit()


async def _configure_author_identity_after_clone(
    *,
    run_dir: Path,
    subargs: list[str],
    repo_url: str,
    author_name: str,
    author_email: str,
    timeout_seconds: int,
) -> None:
    repo_dir = _resolve_clone_destination(run_dir=run_dir, subargs=subargs, repo_url=repo_url)
    if repo_dir is None or not repo_dir.exists():
        return

    env = os.environ.copy()
    env["HOME"] = str(run_dir)
    env["PWD"] = str(repo_dir)
    _run_blocking(
        args=["git", "config", "user.name", author_name],
        cwd=repo_dir,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    _run_blocking(
        args=["git", "config", "user.email", author_email],
        cwd=repo_dir,
        env=env,
        timeout_seconds=timeout_seconds,
    )


def _resolve_clone_destination(
    *,
    run_dir: Path,
    subargs: list[str],
    repo_url: str,
) -> Path | None:
    positional: list[str] = []
    idx = 0
    while idx < len(subargs):
        part = subargs[idx]
        if part == "--":
            positional.extend(subargs[idx + 1 :])
            break
        if part in {"-b", "--branch", "-o", "--origin", "--depth", "--shallow-since", "--jobs"}:
            idx += 2
            continue
        if part.startswith("--") and "=" in part:
            idx += 1
            continue
        if part.startswith("-"):
            idx += 1
            continue
        positional.append(part)
        idx += 1

    if not positional:
        return None
    if len(positional) >= 2:
        destination = positional[1]
    else:
        tail = repo_url.rstrip("/").split("/")[-1].strip()
        destination = tail[:-4] if tail.endswith(".git") else tail
    if not destination:
        return None
    candidate = (
        (run_dir / destination).resolve()
        if not Path(destination).is_absolute()
        else Path(destination).expanduser().resolve()
    )
    if candidate != run_dir and run_dir not in candidate.parents:
        raise ToolValidationError("git clone destination must stay inside session workspace")
    return candidate
