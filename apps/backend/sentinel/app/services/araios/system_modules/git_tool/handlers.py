"""Native module: git — hidden SSH runtime git/GitHub execution."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import posixpath
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from shlex import quote
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal
from app.models import GitAccount
from app.services.runtime.darwin_seatbelt import (
    build_append_seatbelt_tool_roots_script,
    build_seatbelt_command,
    build_seatbelt_profile,
)
from app.services.runtime.linux_bubblewrap import build_bubblewrap_command
from app.services.runtime.ssh_runtime import get_runtime_terminal_manager, runtime_configured
from app.services.runtime.workspace import RemoteWorkspacePaths, workspace_paths
from app.services.secrets import is_invalid_secret
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext

_MAX_GIT_OUTPUT_CHARS = 50_000
_DEFAULT_GIT_TIMEOUT_SECONDS = 600
_MAX_GIT_TIMEOUT_SECONDS = 3600
_FORBIDDEN_GIT_GLOBAL_FLAGS = {"-c", "-C", "--git-dir", "--work-tree"}
_NETWORK_READ_SUBCOMMANDS = {"clone", "fetch", "pull", "ls-remote", "submodule", "request-pull"}
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
    ("api", None),
}
_GH_NETWORK_WRITE_SUBCOMMANDS = {
    ("pr", "create"),
    ("pr", "merge"),
}
_GH_API_WRITE_METHODS = {"POST", "PUT"}


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
    if tokens[0] not in {"git", "gh"}:
        raise ToolValidationError("Only git or selected gh commands are allowed in the git tool.")
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
        if token in _FORBIDDEN_GIT_GLOBAL_FLAGS:
            raise ToolValidationError(f"Git flag '{token}' is not allowed in git")
        if token.startswith("--git-dir=") or token.startswith("--work-tree="):
            raise ToolValidationError("Custom git-dir/work-tree is not allowed in git")


def _git_branch_is_write(subargs: list[str]) -> bool:
    if not subargs:
        return False
    write_flags = {
        "-d",
        "-D",
        "-m",
        "-M",
        "-c",
        "-C",
        "--delete",
        "--move",
        "--copy",
        "--set-upstream-to",
        "--unset-upstream",
    }
    return any(part in write_flags or part.startswith("--set-upstream-to=") for part in subargs)


def _git_tag_is_write(subargs: list[str]) -> bool:
    if not subargs:
        return False
    write_flags = {"-a", "-s", "-u", "-d", "-f", "--annotate", "--sign", "--delete", "--force"}
    return any(part in write_flags for part in subargs) or any(
        not part.startswith("-") for part in subargs
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
    if network_mode is not None:
        return network_mode
    if subcommand == "branch":
        return "write" if _git_branch_is_write(subargs) else "read"
    if subcommand == "tag":
        return "write" if _git_tag_is_write(subargs) else "read"
    if subcommand in _GIT_WRITE_SUBCOMMANDS:
        return "write"
    return "read"


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
        if part.startswith("--method="):
            return part.split("=", 1)[1].strip().upper()
        idx += 1
    return "GET"


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
            "gh pr view/create/merge, and gh api GET/POST/PUT."
        )
    return mode


def _validate_run_command_kind(*, cli_command: str, expect_write: bool) -> None:
    mode = _command_mode(_parse_cli_command(cli_command.strip()))
    if expect_write and mode != "write":
        raise ToolValidationError("Field 'command' must be 'read' for non-write git or gh commands")
    if not expect_write and mode == "write":
        raise ToolValidationError("Field 'command' must be 'write' for write git or gh commands")


def _timeout_seconds(payload: dict[str, Any]) -> int:
    value = payload.get("timeout_seconds", _DEFAULT_GIT_TIMEOUT_SECONDS)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")
    return min(value, _MAX_GIT_TIMEOUT_SECONDS)


def _sandbox_cwd(cwd_raw: Any) -> str:
    if cwd_raw is None:
        return "/workspace"
    if not isinstance(cwd_raw, str) or not cwd_raw.strip():
        raise ToolValidationError("Field 'cwd' must be a non-empty string when provided")
    requested = cwd_raw.strip()
    if requested.startswith("/"):
        candidate = posixpath.normpath(requested)
    else:
        candidate = posixpath.normpath(posixpath.join("/workspace", requested))
    if candidate != "/workspace" and not candidate.startswith("/workspace/"):
        raise ToolValidationError("Field 'cwd' must stay within /workspace")
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
    require_write: bool,
    selector: _AccountSelector | None = None,
) -> _ResolvedAccount | None:
    repo = _parse_repo_ref(repo_url)
    result = await db.execute(select(GitAccount))
    accounts = await _delete_invalid_git_accounts(db, result.scalars().all())

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
        token = requested.token_write if require_write else requested.token_read
        if not token.strip():
            missing_kind = "write token" if require_write else "read token"
            raise ToolValidationError(f"Requested git account is missing required {missing_kind}")
        return _ResolvedAccount(account=requested, repo=repo)

    best: tuple[int, GitAccount] | None = None
    for item in accounts:
        host = (item.host or "").strip().lower()
        if host != repo.host:
            continue
        pattern = (item.scope_pattern or "*").strip().lower() or "*"
        if not (fnmatch.fnmatch(repo.target, pattern) or fnmatch.fnmatch(repo.path, pattern)):
            continue
        token = item.token_write if require_write else item.token_read
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
    require_write: bool,
    selector: _AccountSelector,
) -> GitAccount:
    result = await db.execute(select(GitAccount))
    account = _find_requested_account(
        await _delete_invalid_git_accounts(db, result.scalars().all()), selector
    )
    normalized_host = host.strip().lower()
    account_host = (account.host or "").strip().lower()
    if account_host != normalized_host:
        raise ToolValidationError(
            f"Requested git account does not match GitHub host '{normalized_host}' "
            f"(account host: '{account_host or '<empty>'}')"
        )
    token = account.token_write if require_write else account.token_read
    if not token.strip():
        missing_kind = "write token" if require_write else "read token"
        raise ToolValidationError(f"Requested git account is missing required {missing_kind}")
    return account


async def _delete_invalid_git_accounts(
    db: AsyncSession, accounts: list[GitAccount]
) -> list[GitAccount]:
    valid: list[GitAccount] = []
    deleted = False
    for account in accounts:
        if is_invalid_secret(account.token_read) or is_invalid_secret(account.token_write):
            await db.delete(account)
            deleted = True
            continue
        valid.append(account)
    if deleted:
        await db.commit()
    return valid


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


def _remote_workspace_cwd(paths: RemoteWorkspacePaths, cwd: str) -> str:
    if cwd == "/workspace":
        return paths.workspace
    suffix = cwd.removeprefix("/workspace/")
    return (PurePosixPath(paths.workspace) / suffix).as_posix()


def _build_hidden_runtime_command(
    paths: RemoteWorkspacePaths, *, os_name: str, sandbox: str, cwd: str, tokens: list[str]
) -> str:
    if os_name == "linux" and sandbox == "bubblewrap":
        shell_command = "cd " + quote(cwd) + " && exec " + " ".join(quote(part) for part in tokens)
        return build_bubblewrap_command(paths, ["bash", "-lc", shell_command])
    if os_name == "darwin" and sandbox == "seatbelt":
        profile_path = (PurePosixPath(paths.runtime) / "git.sb").as_posix()
        remote_cwd = _remote_workspace_cwd(paths, cwd)
        shell_command = (
            "cd "
            + quote(remote_cwd)
            + " && export HOME="
            + quote(paths.home)
            + " TMPDIR="
            + quote(paths.tmp)
            + " XDG_CONFIG_HOME="
            + quote((PurePosixPath(paths.home) / ".config").as_posix())
            + " XDG_RUNTIME_DIR="
            + quote(paths.runtime)
            + " && exec "
            + " ".join(quote(part) for part in tokens)
        )
        prelude = "\n".join(
            [
                f"mkdir -p {quote(paths.runtime)}",
                f"cat > {quote(profile_path)} <<'SENTINEL_SEATBELT'",
                build_seatbelt_profile(paths),
                "SENTINEL_SEATBELT",
                build_append_seatbelt_tool_roots_script(
                    paths, profile_path, tools=["/bin/bash", "bash", "git", "gh", "ssh"]
                ),
            ]
        )
        sandbox_command = build_seatbelt_command(
            paths,
            profile_path,
            ["/bin/bash", "--noprofile", "--norc", "-lc", shell_command],
        )
        return "/bin/sh -c " + quote(prelude + "\nexec " + sandbox_command)
    raise ToolValidationError("Git tool requires a runtime with a supported sandbox.")


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
        instance_name=runtime.instance_name, session_factory=runtime.db_session_factory
    ):
        raise ToolValidationError("Runtime SSH target is not configured.")
    terminal_manager = await get_runtime_terminal_manager(
        instance_name=runtime.instance_name, session_factory=runtime.db_session_factory
    )
    environment = await terminal_manager.runtime_environment()
    await terminal_manager.prepare_workspace(session_id)
    paths = workspace_paths(session_id, root=terminal_manager.workspaces_root)
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
            "command": " ".join(tokens),
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
        "command": " ".join(tokens),
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


def _git_auth_env(token: str, repo: _RepoRef) -> dict[str, str]:
    basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    return {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": f"http.https://{repo.host}/.extraheader",
        "GIT_CONFIG_VALUE_1": f"Authorization: Basic {basic}",
    }


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
    token = (account.account.token_write if mode == "write" else account.account.token_read).strip()
    if not token:
        missing_kind = "write token" if mode == "write" else "read token"
        raise ToolValidationError(f"Matching git account is missing required {missing_kind}")
    env = _git_auth_env(token, account.repo)
    if mode == "write":
        env.update(_author_env(account.account))
    result = await _run_hidden_runtime_command(
        runtime=runtime,
        session_id=session_id,
        tokens=tokens,
        cwd=run_dir,
        env=env,
        timeout_seconds=timeout_seconds,
        redactions=[
            token,
            base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii"),
        ],
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
    if primary == "pr" and secondary in {"create", "view", "merge"}:
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


def _validate_gh_clone_destination(*, tokens: list[str]) -> None:
    primary, secondary = _gh_subcommand(tokens)
    if (primary, secondary) != ("repo", "clone"):
        return
    positionals = _positional_arguments(tokens[3:])
    if len(positionals) < 2:
        return
    destination = positionals[1].strip()
    candidate = posixpath.normpath(
        destination if destination.startswith("/") else posixpath.join("/workspace", destination)
    )
    if candidate != "/workspace" and not candidate.startswith("/workspace/"):
        raise ToolValidationError("gh repo clone destination must stay inside /workspace")


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
            "gh pr view/create/merge, and gh api GET/POST/PUT."
        )
    _validate_gh_clone_destination(tokens=tokens)
    host = _extract_gh_host(tokens)
    owner = await _extract_gh_owner(
        runtime=runtime, session_id=session_id, run_dir=run_dir, tokens=tokens
    )
    async with AsyncSessionLocal() as db:
        if owner:
            resolved = await _resolve_git_account(
                db,
                repo_url=f"https://{host}/{owner}/_gh_scope",
                require_write=mode == "write",
                selector=selector,
            )
            if resolved is None:
                required_token = "write token" if mode == "write" else "read token"
                raise ToolValidationError(
                    f"No matching git account is configured for '{host}/{owner}' ({mode} access). "
                    f"Add/update a Git account with matching host/scope and a {required_token}."
                )
            account = resolved.account
        elif selector is not None:
            account = await _resolve_selected_git_account_for_host(
                db,
                host=host,
                require_write=mode == "write",
                selector=selector,
            )
        else:
            raise ToolValidationError(
                "Unable to infer GitHub owner for gh command. Provide an explicit owner or git account."
            )

    token = (account.token_write if mode == "write" else account.token_read).strip()
    env = {
        "GH_TOKEN": token,
        "GITHUB_TOKEN": token,
        "GH_HOST": host,
        "GH_PROMPT_DISABLED": "1",
    }
    result = await _run_hidden_runtime_command(
        runtime=runtime,
        session_id=session_id,
        tokens=tokens,
        cwd=run_dir,
        env=env,
        timeout_seconds=timeout_seconds,
        redactions=[token],
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
    run_dir = _sandbox_cwd(payload.get("cwd"))
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

    subcommand, subcommand_index = _extract_git_subcommand(tokens)
    subargs = tokens[subcommand_index + 1 :]
    _validate_no_forbidden_global_flags(tokens[:subcommand_index])
    _validate_no_forbidden_global_flags(subargs)

    network_mode = _network_mode_for_command(subcommand)
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
                require_write=network_mode == "write",
                selector=selector,
            )
        if account is None:
            required_token = "write token" if network_mode == "write" else "read token"
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
    if subcommand == "commit":
        origin_url = await _resolve_origin_url(runtime, session_id, run_dir)
        async with AsyncSessionLocal() as db:
            account = await _resolve_git_account(
                db,
                repo_url=origin_url,
                require_write=False,
                selector=selector,
            )
        if account is None:
            raise ToolValidationError(
                f"No matching git account is configured for '{_repo_target_label(origin_url)}' "
                "(commit attribution). Add/update a Git account with matching host/scope and a read token."
            )
        env = _author_env(account.account)

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
    _validate_run_command_kind(cli_command=cli_command, expect_write=False)
    return await _handle_run(payload, runtime)


async def handle_write(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    cli_command = payload.get("cli_command")
    if not isinstance(cli_command, str) or not cli_command.strip():
        raise ToolValidationError("Field 'cli_command' must be a non-empty string")
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
    require_write = payload.get("require_write", False)
    if host_raw is not None and (not isinstance(host_raw, str) or not host_raw.strip()):
        raise ToolValidationError("Field 'host' must be a non-empty string when provided")
    if repo_url_raw is not None and (not isinstance(repo_url_raw, str) or not repo_url_raw.strip()):
        raise ToolValidationError("Field 'repo_url' must be a non-empty string when provided")
    if not isinstance(require_write, bool):
        raise ToolValidationError("Field 'require_write' must be a boolean")

    host_filter = host_raw.strip().lower() if isinstance(host_raw, str) else None
    repo_ref = _parse_accounts_repo_ref(repo_url_raw) if isinstance(repo_url_raw, str) else None
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(GitAccount))
        accounts = await _delete_invalid_git_accounts(db, result.scalars().all())

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
        if require_write and not (item.token_write or "").strip():
            continue
        if not require_write and not (item.token_read or "").strip():
            continue
        entries.append(
            {
                "id": str(item.id),
                "name": item.name,
                "host": item.host,
                "scope_pattern": item.scope_pattern,
                "author_name": item.author_name,
                "author_email": item.author_email,
                "has_read_token": bool((item.token_read or "").strip()),
                "has_write_token": bool((item.token_write or "").strip()),
                "matches_repo": _matches_repo(item=item, repo=repo_ref),
            }
        )
    return {"accounts": entries, "total": len(entries)}
