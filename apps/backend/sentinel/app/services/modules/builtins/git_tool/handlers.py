"""Native module: git — workspace container git/GitHub execution."""

from __future__ import annotations

import asyncio
import fnmatch
import posixpath
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from shlex import quote
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal
from app.models import GitAccount
from app.services.runtime.ssh_runtime import (
    get_runtime_terminal_manager,
    runtime_configured,
)
from app.services.runtime.workspace import RemoteWorkspacePaths, workspace_paths
from app.services.secrets import is_invalid_secret
from sentral.errors import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext

from . import credential_broker as credential_broker_module

_MAX_GIT_OUTPUT_CHARS = 50_000
_DEFAULT_GIT_TIMEOUT_SECONDS = 600
_MAX_GIT_TIMEOUT_SECONDS = 3600
_FORBIDDEN_GIT_GLOBAL_FLAGS = {"-c", "-C", "--git-dir", "--work-tree"}
_NETWORK_READ_SUBCOMMANDS = {
    "clone",
    "fetch",
    "pull",
    "ls-remote",
    "submodule",
    "request-pull",
}
_NETWORK_WRITE_SUBCOMMANDS = {"push"}
_GIT_WRITE_SUBCOMMANDS = {
    "add",
    "am",
    "apply",
    "bisect",
    "checkout",
    "cherry-pick",
    "clean",
    "commit",
    "merge",
    "mv",
    "rebase",
    "reset",
    "restore",
    "revert",
    "rm",
    "stash",
    "switch",
    "worktree",
}
_GH_NETWORK_READ_SUBCOMMANDS = {
    ("repo", "clone"),
    ("repo", "list"),
    ("repo", "view"),
    ("pr", "view"),
    ("pr", "list"),
    ("pr", "diff"),
    ("pr", "checks"),
    ("pr", "status"),
    ("search", "repos"),
    ("search", "issues"),
    ("search", "prs"),
    ("search", "code"),
    ("issue", "list"),
    ("issue", "view"),
    ("run", "list"),
    ("run", "view"),
    ("release", "list"),
    ("release", "view"),
    ("api", None),
}
_GH_NETWORK_WRITE_SUBCOMMANDS = {
    ("pr", "create"),
    ("pr", "merge"),
    ("pr", "close"),
    ("pr", "reopen"),
    ("pr", "edit"),
    ("pr", "review"),
    ("pr", "comment"),
    ("pr", "ready"),
    ("issue", "create"),
    ("issue", "edit"),
    ("issue", "close"),
    ("issue", "reopen"),
    ("issue", "comment"),
}
_GH_API_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True, slots=True)
class _RepoRef:
    host: str
    path: str
    target: str


@dataclass(frozen=True, slots=True)
class _ResolvedAccount:
    account: GitAccount
    repo: _RepoRef


@dataclass(frozen=True, slots=True)
class _AccountSelector:
    account_id: UUID | None = None
    account_name: str | None = None


def _session_key(runtime: ToolRuntimeContext) -> str:
    session_id = runtime.runtime_session_id or runtime.session_id
    if session_id is None:
        raise ToolValidationError("Git tool requires an active session context.")
    return str(session_id)


def _account_selector_from_payload(payload: dict[str, Any]) -> _AccountSelector | None:
    account_name_raw = payload.get("git_account_name")
    account_id_raw = payload.get("git_account_id")

    account_name: str | None = None
    if account_name_raw is not None:
        if not isinstance(account_name_raw, str) or not account_name_raw.strip():
            raise ToolValidationError(
                "Field 'git_account_name' must be a non-empty string when provided"
            )
        account_name = account_name_raw.strip()

    account_id: UUID | None = None
    if account_id_raw is not None:
        if not isinstance(account_id_raw, str) or not account_id_raw.strip():
            raise ToolValidationError(
                "Field 'git_account_id' must be a non-empty UUID string when provided"
            )
        try:
            account_id = UUID(account_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'git_account_id' must be a valid UUID string") from exc

    if account_name is not None and account_id is not None:
        raise ToolValidationError("Provide only one of 'git_account_name' or 'git_account_id'")
    if account_name is None and account_id is None:
        return None
    return _AccountSelector(account_id=account_id, account_name=account_name)


def _parse_cli_command(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ToolValidationError(f"Invalid command syntax: {exc}") from exc
    if not tokens:
        raise ToolValidationError("Command is empty")
    if any(x in {"&&", "||", ";", "|", ">", ">>", "<"} for x in tokens):
        raise ToolValidationError(
            "Use one git or gh command per call; shell operators are not supported"
        )
    if tokens[0] not in {"git", "gh"}:
        raise ToolValidationError("Only git or selected gh commands are allowed in the git tool.")
    if tokens[0] == "git":
        normalized = ["git"]
        global_flags = True
        for token in tokens[1:]:
            if global_flags and token.startswith("-C") and len(token) > 2:
                normalized.extend(["-C", token[2:]])
            else:
                normalized.append(token)
            if global_flags and not token.startswith("-"):
                # Separate -C/-c values are handled by the subcommand parser.
                if len(normalized) < 2 or normalized[-2] not in {"-C", "-c"}:
                    global_flags = False
        tokens = normalized
    return tokens


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
        if (
            token in _FORBIDDEN_GIT_GLOBAL_FLAGS
            or token.startswith("-c")
            or token.startswith("--exec-path")
        ):
            raise ToolValidationError(f"Git flag '{token}' is not allowed in git")
        if token.startswith("--git-dir=") or token.startswith("--work-tree="):
            raise ToolValidationError("Custom git-dir/work-tree is not allowed in git")


def _git_branch_is_write(subargs: list[str]) -> bool:
    if not subargs:
        return False
    if any(
        x in subargs
        for x in (
            "--list",
            "-l",
            "--show-current",
            "--contains",
            "--no-contains",
            "--merged",
            "--no-merged",
            "-r",
            "--remotes",
            "-a",
            "--all",
        )
    ):
        return any(
            x in subargs
            for x in (
                "-d",
                "-D",
                "-m",
                "-M",
                "-c",
                "-C",
                "--delete",
                "--move",
                "--copy",
            )
        )
    return True


def _git_tag_is_write(subargs: list[str]) -> bool:
    if any(
        x in subargs
        for x in {"-d", "--delete", "-a", "--annotate", "-s", "--sign", "-f", "--force"}
    ):
        return True
    return bool(subargs) and not any(
        x in subargs
        for x in (
            "--list",
            "-l",
            "--contains",
            "--merged",
            "--no-merged",
            "--points-at",
        )
    )


def _network_mode_for_command(subcommand: str) -> str | None:
    if subcommand in _NETWORK_READ_SUBCOMMANDS:
        return "read"
    if subcommand in _NETWORK_WRITE_SUBCOMMANDS:
        return "write"
    return None


def _git_command_mode(tokens: list[str]) -> str:
    subcommand, idx = _extract_git_subcommand(tokens)
    subargs = tokens[idx + 1 :]
    network_mode = _network_mode_for_command(subcommand)
    if subcommand == "submodule":
        return "read" if not subargs or subargs[0] in {"status", "summary"} else "write"
    if subcommand == "pull":
        return "write"
    if network_mode is not None:
        return network_mode
    if subcommand in {"stash", "worktree"}:
        return "read" if subargs and subargs[0] in {"list", "show"} else "write"
    if subcommand == "reflog" and subargs and subargs[0] in {"expire", "delete", "drop", "write"}:
        return "write"
    if subcommand == "config":
        if any(
            x
            in {
                "set",
                "unset",
                "--unset",
                "--unset-all",
                "--add",
                "--replace-all",
                "--rename-section",
                "--remove-section",
                "rename-section",
                "remove-section",
                "edit",
                "--edit",
                "-e",
            }
            for x in subargs
        ):
            return "write"
        reads = {
            "--get",
            "--get-all",
            "--get-regexp",
            "--get-urlmatch",
            "--list",
            "-l",
            "get",
            "list",
        }
        return (
            "read"
            if any(x in reads for x in subargs) or len(_positional_arguments(subargs)) == 1
            else "write"
        )
    if (
        subcommand in {"add", "clean"}
        and "--no-dry-run" not in subargs
        and any(x in subargs for x in {"--dry-run", "-n"})
    ):
        return "read"
    if subcommand == "remote":
        return (
            "read"
            if not subargs or subargs[0] in {"-v", "--verbose", "get-url", "show"}
            else "write"
        )
    if subcommand == "branch":
        return "write" if _git_branch_is_write(subargs) else "read"
    if subcommand == "tag":
        return "write" if _git_tag_is_write(subargs) else "read"
    if subcommand in _GIT_WRITE_SUBCOMMANDS:
        return "write"
    if subcommand in {
        "status",
        "log",
        "diff",
        "show",
        "rev-parse",
        "ls-files",
        "grep",
        "blame",
        "reflog",
        "merge-base",
        "ls-tree",
        "cat-file",
        "describe",
        "shortlog",
        "rev-list",
        "count-objects",
        "check-ignore",
        "check-attr",
        "diff-files",
        "diff-index",
        "diff-tree",
        "verify-commit",
        "verify-tag",
        "version",
        "help",
    }:
        return "read"
    raise ToolValidationError(f"Unsupported Git operation: {subcommand}")


def _gh_subcommand(tokens: list[str]) -> tuple[str | None, str | None]:
    if len(tokens) < 2:
        return None, None
    primary = tokens[1]
    secondary = tokens[2] if len(tokens) >= 3 and not tokens[2].startswith("-") else None
    return primary, secondary


def _gh_api_method(tokens: list[str]) -> str:
    idx = 2
    while idx < len(tokens):
        part = tokens[idx]
        if part in {"-X", "--method"} and idx + 1 < len(tokens):
            return tokens[idx + 1].strip().upper()
        if part.startswith("-X") and len(part) > 2:
            return part[2:].upper()
        if part.startswith("--method="):
            return part.split("=", 1)[1].strip().upper()
        idx += 1
    return (
        "POST"
        if any(
            x in {"-f", "-F", "--field", "--raw-field", "--input"}
            or x.startswith(("--field=", "--raw-field=", "--input=", "-f", "-F"))
            for x in tokens[2:]
        )
        else "GET"
    )


def _gh_network_mode(tokens: list[str]) -> str | None:
    primary, secondary = _gh_subcommand(tokens)
    if primary == "api":
        method = _gh_api_method(tokens)
        if method == "GET":
            return "read"
        if method in _GH_API_WRITE_METHODS:
            return "write"
        return None
    if (primary, secondary) in _GH_NETWORK_READ_SUBCOMMANDS:
        return "read"
    if (primary, secondary) in _GH_NETWORK_WRITE_SUBCOMMANDS:
        return "write"
    return None


def _command_mode(tokens: list[str]) -> str:
    if tokens[0] == "git":
        return _git_command_mode(tokens)
    mode = _gh_network_mode(tokens)
    if mode is None:
        raise ToolValidationError(
            "Unsupported gh command in git. Supported: gh repo clone/list/view, "
            "gh search, PR/issue read and write operations, run/release inspection, and gh api GET/POST/PUT/PATCH/DELETE."
        )
    return mode


def _validate_run_command_kind(*, cli_command: str, expect_write: bool) -> None:
    tokens = _parse_cli_command(cli_command.strip())
    mode = _command_mode(tokens)
    prefix = "gh_" if tokens[0] == "gh" else ""
    if expect_write and mode != "write":
        raise ToolValidationError(f"Use action={prefix}read for this inspection command")
    if not expect_write and mode == "write":
        raise ToolValidationError(f"Use action={prefix}write for this mutating command")


def _timeout_seconds(payload: dict[str, Any]) -> int:
    value = payload.get("timeout_seconds", _DEFAULT_GIT_TIMEOUT_SECONDS)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")
    return min(value, _MAX_GIT_TIMEOUT_SECONDS)


def _project_cwd(cwd_raw: Any, project: str) -> str:
    if cwd_raw is None:
        return project
    if not isinstance(cwd_raw, str) or not cwd_raw.strip():
        raise ToolValidationError("Field 'cwd' must be a non-empty string when provided")
    candidate = posixpath.normpath(posixpath.join(project, cwd_raw.strip()))
    if "\x00" in candidate:
        raise ToolValidationError("Invalid working directory")
    return candidate


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


def _first_positional_argument(args: list[str]) -> str | None:
    positionals = _positional_arguments(args)
    return positionals[0] if positionals else None


def _looks_like_repo_url(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    if "://" in candidate:
        parsed = urlparse(candidate)
        return parsed.scheme in {"http", "https", "ssh"} and bool(parsed.hostname)
    return candidate.startswith("git@") and ":" in candidate


def _extract_clone_repo_url(subargs: list[str]) -> str:
    repo_url = _first_positional_argument(subargs)
    if repo_url is None:
        raise ToolValidationError("git clone requires a repository URL")
    if not _looks_like_repo_url(repo_url):
        raise ToolValidationError("git clone repository argument must be a URL or ssh repo spec")
    return repo_url


def _extract_request_pull_repo_url(subargs: list[str]) -> tuple[str, str]:
    positionals = _positional_arguments(subargs)
    if len(positionals) < 2:
        raise ToolValidationError("git request-pull requires <start> and <url> arguments")
    upstream = positionals[1]
    if _looks_like_repo_url(upstream):
        return upstream, "origin"
    return "", upstream


def _parse_repo_ref(repo_url: str) -> _RepoRef:
    raw = repo_url.strip()
    if not raw:
        raise ToolValidationError("Repository URL is empty")
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").strip().lower()
        path = (parsed.path or "").strip().lstrip("/")
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
    return sum(1 for char in pattern if char not in {"*", "?", "["})


async def _resolve_git_account(
    db: AsyncSession,
    *,
    repo_url: str,
    selector: _AccountSelector | None = None,
) -> _ResolvedAccount | None:
    repo = _parse_repo_ref(repo_url)
    result = await db.execute(select(GitAccount))
    accounts = _usable_git_accounts(result.scalars().all())

    if selector is not None:
        requested = _find_requested_account(accounts, selector)
        host = (requested.host or "").strip().lower()
        if host != repo.host:
            raise ToolValidationError(
                f"Requested git account does not match repository host '{repo.host}' "
                f"(account host: '{host or '<empty>'}')"
            )
        pattern = (requested.scope_pattern or "*").strip().lower() or "*"
        if not (fnmatch.fnmatch(repo.target, pattern) or fnmatch.fnmatch(repo.path, pattern)):
            raise ToolValidationError(
                f"Requested git account scope does not match repository '{repo.target}' "
                f"(scope: '{requested.scope_pattern}')"
            )
        token = requested.token
        if not token.strip():
            raise ToolValidationError("Requested git account is missing a token")
        return _ResolvedAccount(account=requested, repo=repo)

    best: tuple[int, GitAccount] | None = None
    for item in accounts:
        host = (item.host or "").strip().lower()
        if host != repo.host:
            continue
        pattern = (item.scope_pattern or "*").strip().lower() or "*"
        if not (fnmatch.fnmatch(repo.target, pattern) or fnmatch.fnmatch(repo.path, pattern)):
            continue
        token = item.token
        if not token.strip():
            continue
        score = _specificity(pattern)
        if best is None or score > best[0]:
            best = (score, item)
    return _ResolvedAccount(account=best[1], repo=repo) if best else None


def _find_requested_account(accounts: list[GitAccount], selector: _AccountSelector) -> GitAccount:
    if selector.account_id is not None:
        for item in accounts:
            if item.id == selector.account_id:
                return item
        raise ToolValidationError(f"Requested git account id '{selector.account_id}' was not found")
    if selector.account_name is not None:
        selected = selector.account_name.casefold()
        for item in accounts:
            if (item.name or "").strip().casefold() == selected:
                return item
        raise ToolValidationError(f"Requested git account '{selector.account_name}' was not found")
    raise ToolValidationError("Explicit git account selection is required")


async def _resolve_selected_git_account_for_host(
    db: AsyncSession,
    *,
    host: str,
    selector: _AccountSelector,
) -> GitAccount:
    result = await db.execute(select(GitAccount))
    account = _find_requested_account(_usable_git_accounts(result.scalars().all()), selector)
    normalized_host = host.strip().lower()
    account_host = (account.host or "").strip().lower()
    if account_host != normalized_host:
        raise ToolValidationError(
            f"Requested git account does not match GitHub host '{normalized_host}' "
            f"(account host: '{account_host or '<empty>'}')"
        )
    token = account.token
    if not token.strip():
        raise ToolValidationError("Requested git account is missing a token")
    return account


def _usable_git_accounts(accounts: list[GitAccount]) -> list[GitAccount]:
    # Keep unreadable credentials available in Settings for replacement.
    return [account for account in accounts if not is_invalid_secret(account.token)]


def _truncate_output(value: str | None) -> str:
    text = value or ""
    if len(text) <= _MAX_GIT_OUTPUT_CHARS:
        return text
    return f"{text[:_MAX_GIT_OUTPUT_CHARS]}\n...[truncated]"


def _redact(text: str, redactions: list[str] | None) -> str:
    output = text
    for secret in redactions or []:
        if secret:
            output = output.replace(secret, "***")
    return output


def _build_hidden_runtime_command(
    paths: RemoteWorkspacePaths,
    *,
    os_name: str,
    sandbox: str,
    cwd: str,
    tokens: list[str],
) -> str:
    if os_name != "linux" or sandbox != "container":
        raise ToolValidationError("Git requires an attached workspace container.")
    return (
        "cd "
        + quote(_project_cwd(cwd, paths.workspace))
        + " && exec "
        + " ".join(quote(part) for part in tokens)
    )


async def _run_hidden_runtime_command(
    *,
    runtime: ToolRuntimeContext,
    session_id: str,
    tokens: list[str],
    cwd: str,
    env: dict[str, str] | None,
    timeout_seconds: int,
    redactions: list[str] | None = None,
) -> dict[str, Any]:
    if runtime.instance_name is None:
        raise ToolValidationError("Git tool requires an active instance context.")
    if not await runtime_configured(
        session_id=runtime.runtime_session_id or runtime.session_id,
        instance_name=runtime.instance_name,
        session_factory=runtime.db_session_factory,
    ):
        raise ToolValidationError(
            "No usable workspace attached. Ask the user to attach a workspace from the session toolbar before using this tool."
        )
    terminal_manager = await get_runtime_terminal_manager(
        session_id=runtime.runtime_session_id or runtime.session_id,
        instance_name=runtime.instance_name,
        session_factory=runtime.db_session_factory,
    )
    environment = await terminal_manager.runtime_environment()
    await terminal_manager.prepare_workspace(session_id)
    paths = workspace_paths(session_id, root=terminal_manager.workspace_location)
    runtime_command = _build_hidden_runtime_command(
        paths,
        os_name=environment.os,
        sandbox=environment.sandbox,
        cwd=cwd,
        tokens=tokens,
    )
    try:
        result = await terminal_manager.ssh.run(runtime_command, timeout=timeout_seconds, env=env)
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "returncode": -1,
            "timed_out": True,
            "stdout": "",
            "stderr": f"Git command timed out after {timeout_seconds}s",
            "cwd": cwd,
            "command": shlex.join(tokens),
            "argv": tokens,
        }
    stderr = _redact(result.stderr, redactions)
    if result.exit_status == 127:
        stderr = f"Required executable '{tokens[0]}' is not available in the runtime PATH.\n{stderr}".strip()
    return {
        "ok": result.exit_status == 0,
        "returncode": int(result.exit_status if result.exit_status is not None else -1),
        "timed_out": False,
        "stdout": _truncate_output(_redact(result.stdout, redactions)),
        "stderr": _truncate_output(stderr),
        "cwd": cwd,
        "command": shlex.join(tokens),
        "argv": tokens,
    }


async def _resolve_origin_url(
    runtime: ToolRuntimeContext,
    session_id: str,
    run_dir: str,
    remote_name: str = "origin",
) -> str:
    result = await _run_hidden_runtime_command(
        runtime=runtime,
        session_id=session_id,
        tokens=["git", "remote", "get-url", remote_name],
        cwd=run_dir,
        env=None,
        timeout_seconds=30,
    )
    if result["returncode"] != 0:
        stderr = (result.get("stderr") or "").strip()
        lowered = stderr.lower()
        if "not a git repository" in lowered:
            raise ToolValidationError(
                "git fetch/pull/push requires a git repository in the selected cwd. "
                "Run `git clone <repo>` first or set `cwd` to an existing repository in the session workspace."
            )
        if "no such remote" in lowered or "could not get url" in lowered:
            raise ToolValidationError(
                f"Git remote '{remote_name}' was not found in this repository."
            )
        detail = stderr.splitlines()[0] if stderr else ""
        raise ToolValidationError(
            "Unable to resolve repository remote URL for account matching."
            + (f" (git: {detail})" if detail else "")
        )
    repo_url = (result.get("stdout") or "").strip()
    if not repo_url:
        raise ToolValidationError("Repository remote URL is empty")
    return repo_url


async def _resolve_network_repo_url(
    *,
    runtime: ToolRuntimeContext,
    session_id: str,
    run_dir: str,
    subcommand: str,
    subargs: list[str],
) -> str:
    if subcommand == "clone":
        return _extract_clone_repo_url(subargs)
    if subcommand == "ls-remote":
        candidate = _first_positional_argument(subargs)
        if candidate and _looks_like_repo_url(candidate):
            return candidate
    if subcommand == "request-pull":
        repo_url, remote_name = _extract_request_pull_repo_url(subargs)
        return repo_url or await _resolve_origin_url(
            runtime, session_id, run_dir, remote_name=remote_name
        )
    remote_name = _first_positional_argument(subargs) or "origin"
    return await _resolve_origin_url(runtime, session_id, run_dir, remote_name=remote_name)


def _author_env(account: GitAccount) -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": account.author_name,
        "GIT_AUTHOR_EMAIL": account.author_email,
        "GIT_COMMITTER_NAME": account.author_name,
        "GIT_COMMITTER_EMAIL": account.author_email,
    }


async def _run_network_git(
    *,
    runtime: ToolRuntimeContext,
    session_id: str,
    account: _ResolvedAccount,
    run_dir: str,
    tokens: list[str],
    timeout_seconds: int,
    mode: str,
) -> dict[str, Any]:

    terminal = await get_runtime_terminal_manager(
        session_id=runtime.runtime_session_id or runtime.session_id,
        instance_name=runtime.instance_name,
        session_factory=runtime.db_session_factory,
    )
    result = await credential_broker_module.run_brokered(
        terminal=terminal,
        tokens=tokens,
        cwd=run_dir,
        account=account.account,
        mode=mode,
        timeout=timeout_seconds,
        repo=account.repo.path,
        env=(
            {
                k: v
                for k, v in _author_env(account.account).items()
                if k.startswith("GIT_COMMITTER_")
            }
            if _extract_git_subcommand(tokens)[0] == "pull"
            else None
        ),
    )
    result["network_mode"] = mode
    result["account"] = _account_payload(account.account)
    return result


def _extract_gh_host(tokens: list[str]) -> str:
    idx = 1
    while idx < len(tokens):
        part = tokens[idx]
        if part == "--hostname" and idx + 1 < len(tokens):
            return tokens[idx + 1].strip().lower()
        if part.startswith("--hostname="):
            return part.split("=", 1)[1].strip().lower()
        idx += 1
    return "github.com"


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


def _extract_owner_from_gh_api_endpoint(endpoint: str) -> str | None:
    value = endpoint.strip()
    if not value:
        return None
    if "://" in value:
        parsed = urlparse(value)
        value = parsed.path or ""
    parts = [part for part in value.strip().lstrip("/").split("/") if part]
    if len(parts) >= 3 and parts[0] == "orgs" and parts[2] == "repos":
        return parts[1]
    if len(parts) >= 3 and parts[0] == "users" and parts[2] == "repos":
        return parts[1]
    if len(parts) >= 3 and parts[0] == "repos":
        return parts[1]
    return None


async def _extract_gh_owner(
    *,
    runtime: ToolRuntimeContext,
    session_id: str,
    run_dir: str,
    tokens: list[str],
) -> str | None:
    primary, secondary = _gh_subcommand(tokens)
    if primary == "repo" and secondary == "list":
        return _normalize_owner(_first_positional_argument(tokens[3:]))
    if primary == "repo" and secondary == "clone":
        slug = _first_positional_argument(tokens[3:])
        if slug and "/" in slug:
            return _normalize_owner(slug.split("/", 1)[0])
        explicit_repo = _extract_option_value(tokens[3:], {"-R", "--repo"})
        if explicit_repo and "/" in explicit_repo:
            return _normalize_owner(explicit_repo.split("/", 1)[0])
        return None
    if primary == "repo" and secondary == "view":
        slug = _first_positional_argument(tokens[3:])
        if slug and "/" in slug:
            return _normalize_owner(slug.split("/", 1)[0])
        explicit_repo = _extract_option_value(tokens[3:], {"-R", "--repo"})
        if explicit_repo and "/" in explicit_repo:
            return _normalize_owner(explicit_repo.split("/", 1)[0])
        origin_url = await _resolve_origin_url(runtime, session_id, run_dir)
        repo = _parse_repo_ref(origin_url)
        return _normalize_owner(repo.path.split("/", 1)[0])
    if primary in {"pr", "issue", "run", "release"}:
        explicit_repo = _extract_option_value(tokens[3:], {"-R", "--repo"})
        if explicit_repo and "/" in explicit_repo:
            return _normalize_owner(explicit_repo.split("/", 1)[0])
        origin_url = await _resolve_origin_url(runtime, session_id, run_dir)
        repo = _parse_repo_ref(origin_url)
        return _normalize_owner(repo.path.split("/", 1)[0])
    if primary == "api":
        endpoint = _first_positional_argument(tokens[2:])
        return _normalize_owner(_extract_owner_from_gh_api_endpoint(endpoint or ""))
    return None


async def _execute_gh_command(
    *,
    runtime: ToolRuntimeContext,
    session_id: str,
    run_dir: str,
    tokens: list[str],
    timeout_seconds: int,
    selector: _AccountSelector | None,
) -> dict[str, Any]:
    mode = _gh_network_mode(tokens)
    if mode not in {"read", "write"}:
        raise ToolValidationError(
            "Unsupported gh command in git. Supported: gh repo clone/list/view, "
            "gh search, PR/issue read and write operations, run/release inspection, and gh api GET/POST/PUT/PATCH/DELETE."
        )
    host = _extract_gh_host(tokens)
    owner = await _extract_gh_owner(
        runtime=runtime, session_id=session_id, run_dir=run_dir, tokens=tokens
    )
    async with AsyncSessionLocal() as db:
        if selector is not None:
            account = await _resolve_selected_git_account_for_host(db, host=host, selector=selector)
        else:
            available = _usable_git_accounts((await db.execute(select(GitAccount))).scalars().all())
            matching = [item for item in available if item.host.lower() == host]
            if owner:
                matching = [
                    item
                    for item in matching
                    if fnmatch.fnmatch(
                        f"{host}/{owner}/_gh_scope", (item.scope_pattern or "*").lower()
                    )
                    or (item.scope_pattern or "").lower().startswith(f"{host}/{owner}/")
                ]
            if len(matching) != 1:
                raise ToolValidationError("Choose a matching git account for this GitHub command")
            account = matching[0]

    terminal = await get_runtime_terminal_manager(
        session_id=runtime.runtime_session_id or runtime.session_id,
        instance_name=runtime.instance_name,
        session_factory=runtime.db_session_factory,
    )
    result = await credential_broker_module.run_brokered(
        terminal=terminal,
        tokens=tokens,
        cwd=run_dir,
        account=account,
        mode=mode,
        timeout=timeout_seconds,
    )
    result["network_mode"] = mode
    result["account"] = _account_payload(account)
    return result


def _account_payload(account: GitAccount) -> dict[str, str]:
    return {
        "id": str(account.id),
        "name": account.name,
        "host": account.host,
        "scope_pattern": account.scope_pattern,
    }


async def _handle_run(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    session_id = _session_key(runtime)
    cli_command = payload.get("cli_command")
    if not isinstance(cli_command, str) or not cli_command.strip():
        raise ToolValidationError("Field 'cli_command' must be a non-empty string")
    timeout_seconds = _timeout_seconds(payload)
    terminal = await get_runtime_terminal_manager(
        session_id=runtime.runtime_session_id or runtime.session_id,
        instance_name=runtime.instance_name,
        session_factory=runtime.db_session_factory,
    )
    project = terminal.workspace_location.directory
    run_dir = _project_cwd(payload.get("cwd"), project)
    selector = _account_selector_from_payload(payload)
    tokens = _parse_cli_command(cli_command.strip())

    if tokens[0] == "gh":
        return await _execute_gh_command(
            runtime=runtime,
            session_id=session_id,
            run_dir=run_dir,
            tokens=tokens,
            timeout_seconds=timeout_seconds,
            selector=selector,
        )

    while "-C" in tokens[1 : _extract_git_subcommand(tokens)[1]]:
        index = tokens.index("-C")
        run_dir = _project_cwd(tokens[index + 1], run_dir)
        tokens = tokens[:index] + tokens[index + 2 :]
    subcommand, subcommand_index = _extract_git_subcommand(tokens)
    subargs = tokens[subcommand_index + 1 :]
    _validate_no_forbidden_global_flags(tokens[:subcommand_index])

    network_mode = _network_mode_for_command(subcommand)
    if subcommand == "clone":
        args = _positional_arguments(subargs)
        if args and not _looks_like_repo_url(args[0]):
            network_mode = None
    if network_mode is not None:
        repo_url = await _resolve_network_repo_url(
            runtime=runtime,
            session_id=session_id,
            run_dir=run_dir,
            subcommand=subcommand,
            subargs=subargs,
        )
        async with AsyncSessionLocal() as db:
            account = await _resolve_git_account(
                db,
                repo_url=repo_url,
                selector=selector,
            )
        if account is None:
            required_token = "token"
            raise ToolValidationError(
                f"No matching git account is configured for '{_repo_target_label(repo_url)}' "
                f"({network_mode} access). Add/update a Git account with matching host/scope and a {required_token}."
            )
        return await _run_network_git(
            runtime=runtime,
            session_id=session_id,
            account=account,
            run_dir=run_dir,
            tokens=tokens,
            timeout_seconds=timeout_seconds,
            mode=network_mode,
        )

    env: dict[str, str] | None = None
    if subcommand in {
        "commit",
        "cherry-pick",
        "revert",
        "merge",
        "rebase",
        "am",
        "tag",
        "pull",
    }:
        origin_url = await _resolve_origin_url(runtime, session_id, run_dir)
        async with AsyncSessionLocal() as db:
            account = await _resolve_git_account(
                db,
                repo_url=origin_url,
                selector=selector,
            )
        if account is None:
            raise ToolValidationError(
                f"No matching git account is configured for '{_repo_target_label(origin_url)}' "
                "(commit attribution). Add/update a Git account with matching host/scope and a token."
            )
        env = _author_env(account.account)
        # Revert creates a new authored commit; cherry-pick/rebase preserve the source author.
        if subcommand not in {"commit", "revert"}:
            env = {k: v for k, v in env.items() if k.startswith("GIT_COMMITTER_")}

    result = await _run_hidden_runtime_command(
        runtime=runtime,
        session_id=session_id,
        tokens=tokens,
        cwd=run_dir,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    if subcommand == "commit" and env is not None:
        result["author"] = {
            "name": env["GIT_AUTHOR_NAME"],
            "email": env["GIT_AUTHOR_EMAIL"],
        }
    return result


async def handle_read(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    cli_command = payload.get("cli_command")
    if not isinstance(cli_command, str) or not cli_command.strip():
        raise ToolValidationError("Field 'cli_command' must be a non-empty string")
    if _parse_cli_command(cli_command)[0] != "git":
        raise ToolValidationError("Use action=gh_read or gh_write for gh commands")
    _validate_run_command_kind(cli_command=cli_command, expect_write=False)
    return await _handle_run(payload, runtime)


async def handle_write(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    cli_command = payload.get("cli_command")
    if not isinstance(cli_command, str) or not cli_command.strip():
        raise ToolValidationError("Field 'cli_command' must be a non-empty string")
    if _parse_cli_command(cli_command)[0] != "git":
        raise ToolValidationError("Use action=gh_read or gh_write for gh commands")
    _validate_run_command_kind(cli_command=cli_command, expect_write=True)
    return await _handle_run(payload, runtime)


def _parse_accounts_repo_ref(repo_url: str) -> dict[str, str]:
    repo = _parse_repo_ref(repo_url)
    return {"host": repo.host, "path": repo.path, "target": repo.target}


def _matches_repo(*, item: GitAccount, repo: dict[str, str] | None) -> bool:
    if repo is None:
        return True
    host = (item.host or "").strip().lower()
    if host != repo["host"]:
        return False
    pattern = (item.scope_pattern or "*").strip().lower() or "*"
    return fnmatch.fnmatch(repo["target"], pattern) or fnmatch.fnmatch(repo["path"], pattern)


async def handle_accounts(payload: dict[str, Any]) -> dict[str, Any]:
    host_raw = payload.get("host")
    repo_url_raw = payload.get("repo_url")
    if host_raw is not None and (not isinstance(host_raw, str) or not host_raw.strip()):
        raise ToolValidationError("Field 'host' must be a non-empty string when provided")
    if repo_url_raw is not None and (not isinstance(repo_url_raw, str) or not repo_url_raw.strip()):
        raise ToolValidationError("Field 'repo_url' must be a non-empty string when provided")

    host_filter = host_raw.strip().lower() if isinstance(host_raw, str) else None
    repo_ref = _parse_accounts_repo_ref(repo_url_raw) if isinstance(repo_url_raw, str) else None
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(GitAccount))
        accounts = _usable_git_accounts(result.scalars().all())

    accounts.sort(
        key=lambda item: (
            item.updated_at or datetime.min.replace(tzinfo=UTC),
            (item.name or "").casefold(),
        ),
        reverse=True,
    )

    entries: list[dict[str, Any]] = []
    for item in accounts:
        host = (item.host or "").strip().lower()
        if host_filter and host != host_filter:
            continue
        if not _matches_repo(item=item, repo=repo_ref):
            continue
        if not (item.token or "").strip():
            continue
        entries.append(
            {
                "id": str(item.id),
                "name": item.name,
                "host": item.host,
                "scope_pattern": item.scope_pattern,
                "author_name": item.author_name,
                "author_email": item.author_email,
                "has_token": bool((item.token or "").strip()),
                "matches_repo": _matches_repo(item=item, repo=repo_ref),
            }
        )
    return {"accounts": entries, "total": len(entries)}


async def _handle_gh(payload, runtime, *, write):
    command = payload.get("cli_command", "")
    if not isinstance(command, str) or _parse_cli_command(command)[0] != "gh":
        raise ToolValidationError("GitHub actions require one gh command")
    _validate_run_command_kind(cli_command=command, expect_write=write)
    return await _handle_run(payload, runtime)


async def handle_gh_read(payload, runtime):
    return await _handle_gh(payload, runtime, write=False)


async def handle_gh_write(payload, runtime):
    return await _handle_gh(payload, runtime, write=True)


async def handle_run_script(payload, runtime):

    script = payload.get("script")
    if not isinstance(script, str) or not script.strip() or "\x00" in script:
        raise ToolValidationError(
            "Field 'script' must be a non-empty shell script without NUL bytes"
        )
    session_id = _session_key(runtime)
    timeout = _timeout_seconds(payload)
    terminal = await get_runtime_terminal_manager(
        session_id=runtime.runtime_session_id or runtime.session_id,
        instance_name=runtime.instance_name,
        session_factory=runtime.db_session_factory,
    )
    cwd = _project_cwd(payload.get("cwd"), terminal.workspace_location.directory)
    selector = _account_selector_from_payload(payload)
    async with AsyncSessionLocal() as db:
        if selector is not None:
            accounts = _usable_git_accounts((await db.execute(select(GitAccount))).scalars().all())
            account = _find_requested_account(accounts, selector)
        else:
            origin = await _resolve_origin_url(runtime, session_id, cwd)
            resolved = await _resolve_git_account(db, repo_url=origin)
            account = resolved.account if resolved else None
    if account is None or not (account.token or "").strip():
        raise ToolValidationError(
            "No usable Git account found; select git_account_name or git_account_id"
        )
    result = await credential_broker_module.run_brokered(
        terminal=terminal,
        tokens=["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        cwd=cwd,
        account=account,
        mode="write",
        timeout=timeout,
        git_identity={"name": account.author_name, "email": account.author_email},
    )
    result["account"] = _account_payload(account)
    return result


async def handle_repos(payload, runtime):

    query = str(payload.get("query") or "").casefold()
    selector = _account_selector_from_payload(payload)
    async with AsyncSessionLocal() as db:
        accounts = _usable_git_accounts((await db.execute(select(GitAccount))).scalars().all())
    if selector:
        accounts = [_find_requested_account(accounts, selector)]
    results = []
    errors = []
    truncated = False
    async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
        for account in accounts:
            host = account.host.strip().lower()
            base = "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
            try:
                for page in range(1, 101):
                    response = await client.get(
                        base + "/user/repos",
                        params={
                            "affiliation": "owner,collaborator,organization_member",
                            "per_page": 100,
                            "page": page,
                        },
                        headers={
                            "Authorization": f"Bearer {account.token}",
                            "Accept": "application/vnd.github+json",
                        },
                    )
                    response.raise_for_status()
                    repos = response.json()
                    for repo in repos:
                        name = repo["full_name"]
                        if query not in name.casefold() or not fnmatch.fnmatch(
                            f"{host}/{name}", account.scope_pattern or "*"
                        ):
                            continue
                        if len(results) >= 1000:
                            truncated = True
                            continue
                        results.append(
                            {
                                "name": name,
                                "url": repo["html_url"],
                                "private": repo["private"],
                                "permissions": repo.get("permissions", {}),
                                "account_id": str(account.id),
                                "account_name": account.name,
                            }
                        )
                    if len(repos) < 100:
                        break
                else:
                    truncated = True
            except Exception as exc:
                errors.append(
                    {
                        "account": account.name,
                        "error": str(exc).replace(account.token, "***"),
                    }
                )
    return {
        "repositories": results,
        "errors": errors,
        "total": len(results),
        "truncated": truncated,
    }
