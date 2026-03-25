"""Native module: git_exec — git/GitHub CLI execution with managed credentials."""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import hashlib
import logging
import os
import shlex
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import AsyncSessionLocal
from app.models import GitAccount, Session
from app.services.runtime.session_runtime import ensure_runtime_layout, runtime_workspace_dir
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.runtime_context import require_session_id

logger = logging.getLogger(__name__)

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
    ("pr", "merge"),
}
_GH_API_WRITE_METHODS = {"POST", "PUT"}
_DEFAULT_GIT_TIMEOUT_SECONDS = 600
_MAX_GIT_TIMEOUT_SECONDS = 3600
# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers — session / account resolution
# ---------------------------------------------------------------------------

async def _ensure_session_exists(
    session_id: UUID,
) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        session = result.scalars().first()
        if session is None:
            raise ToolValidationError("Session not found")


def _account_selector_from_payload(payload: dict[str, Any]) -> _AccountSelector | None:
    account_name_raw = payload.get("git_account_name")
    account_id_raw = payload.get("git_account_id")

    account_name: str | None = None
    if account_name_raw is not None:
        if not isinstance(account_name_raw, str) or not account_name_raw.strip():
            raise ToolValidationError("Field 'git_account_name' must be a non-empty string when provided")
        account_name = account_name_raw.strip()

    account_id: UUID | None = None
    if account_id_raw is not None:
        if not isinstance(account_id_raw, str) or not account_id_raw.strip():
            raise ToolValidationError("Field 'git_account_id' must be a non-empty UUID string when provided")
        try:
            account_id = UUID(account_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError("Field 'git_account_id' must be a valid UUID string") from exc

    if account_name is not None and account_id is not None:
        raise ToolValidationError("Provide only one of 'git_account_name' or 'git_account_id'")

    if account_name is None and account_id is None:
        return None
    return _AccountSelector(account_id=account_id, account_name=account_name)


# ---------------------------------------------------------------------------
# Command parsing
# ---------------------------------------------------------------------------

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
            "Supported gh commands in git_exec: `gh repo list`, `gh repo view`, `gh pr view`, `gh pr create`, `gh pr merge`, `gh api` (GET/POST/PUT)."
        )
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
            raise ToolValidationError(f"Git flag '{token}' is not allowed in git_exec")
        if token.startswith("--git-dir=") or token.startswith("--work-tree="):
            raise ToolValidationError("Custom git-dir/work-tree is not allowed in git_exec")


def _normalize_command(command: str) -> str:
    return " ".join(command.strip().split()).lower()


def _command_hash(command: str) -> str:
    normalized = _normalize_command(command)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# gh helpers
# ---------------------------------------------------------------------------

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
            raise ToolValidationError("gh api supports GET, POST, and PUT in git_exec")
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
    if primary == "pr" and secondary in {"create", "view", "merge"}:
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


# ---------------------------------------------------------------------------
# Network mode / URL resolution
# ---------------------------------------------------------------------------

def _network_mode_for_command(subcommand: str) -> str | None:
    if subcommand in _NETWORK_READ_SUBCOMMANDS:
        return "read"
    if subcommand in _NETWORK_WRITE_SUBCOMMANDS:
        return "write"
    return None


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


# ---------------------------------------------------------------------------
# Repo ref / account matching
# ---------------------------------------------------------------------------

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
    selector: _AccountSelector | None = None,
) -> _ResolvedAccount | None:
    repo = _parse_repo_ref(repo_url)
    result = await db.execute(select(GitAccount))
    accounts = result.scalars().all()

    requested_account: GitAccount | None = None
    if selector is not None:
        if selector.account_id is not None:
            requested_account = next(
                (item for item in accounts if item.id == selector.account_id),
                None,
            )
            if requested_account is None:
                raise ToolValidationError(
                    f"Requested git account id '{selector.account_id}' was not found"
                )
        elif selector.account_name is not None:
            selected_name = selector.account_name.casefold()
            requested_account = next(
                (
                    item
                    for item in accounts
                    if (item.name or "").strip().casefold() == selected_name
                ),
                None,
            )
            if requested_account is None:
                raise ToolValidationError(
                    f"Requested git account '{selector.account_name}' was not found"
                )

    if requested_account is not None:
        host = (requested_account.host or "").strip().lower()
        if host != repo.host:
            raise ToolValidationError(
                "Requested git account does not match repository host "
                f"'{repo.host}' (account host: '{host or '<empty>'}')"
            )
        pattern_raw = (requested_account.scope_pattern or "*").strip().lower() or "*"
        matches_scope = fnmatch.fnmatch(repo.target, pattern_raw) or fnmatch.fnmatch(repo.path, pattern_raw)
        if not matches_scope:
            raise ToolValidationError(
                "Requested git account scope does not match repository "
                f"'{repo.target}' (scope: '{requested_account.scope_pattern}')"
            )
        token = requested_account.token_write if require_write else requested_account.token_read
        if not token.strip():
            missing_kind = "write token" if require_write else "read token"
            raise ToolValidationError(
                f"Requested git account is missing required {missing_kind}"
            )
        return _ResolvedAccount(account=requested_account, repo=repo)

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


# ---------------------------------------------------------------------------
# Subprocess execution
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Local / network git execution
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# gh command execution
# ---------------------------------------------------------------------------

async def _execute_gh_command(
    *,
    session_id: UUID,
    workspace_dir: Path,
    run_dir: Path,
    tokens: list[str],
    timeout_seconds: int,
    account_selector: _AccountSelector | None,
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
                "`gh pr view`, `gh pr create`, `gh pr merge`, `gh api <endpoint>` (GET/POST/PUT)."
            )
        raise ToolValidationError(
            "Unsupported gh command in git_exec. "
            "Supported: `gh repo list <org>`, `gh repo view <org>/<repo>`, "
            "`gh pr view`, `gh pr create`, `gh pr merge`, `gh api <endpoint>` (GET/POST/PUT)."
        )

    owner = _extract_gh_owner(tokens, run_dir=run_dir)
    if not owner:
        raise ToolValidationError(
            "Unable to infer GitHub owner for gh command. "
            "Provide explicit owner (for example: `gh repo list <org>`, `gh repo view <org>/<repo>`, "
            "or `gh api /orgs/<org>/repos`)."
        )

    scope_repo_url = f"https://{host}/{owner}/_gh_scope"
    async with AsyncSessionLocal() as db:
        account = await _resolve_git_account(
            db,
            repo_url=scope_repo_url,
            require_write=mode == "write",
            selector=account_selector,
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
    return result


# ---------------------------------------------------------------------------
# Approval helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# git_accounts_available helpers (from git_accounts_available.py)
# ---------------------------------------------------------------------------

def _parse_accounts_repo_ref(repo_url: str) -> dict[str, str]:
    raw = repo_url.strip()
    if not raw:
        raise ToolValidationError("Field 'repo_url' must be non-empty")

    host = ""
    path = ""
    if "://" in raw:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").strip().lower()
        path = (parsed.path or "").strip().lstrip("/")
    elif raw.startswith("git@") and ":" in raw:
        host = raw[4:].split(":", 1)[0].strip().lower()
        path = raw.split(":", 1)[1].strip().lstrip("/")
    else:
        raise ToolValidationError("Field 'repo_url' must be a supported git URL")

    if path.endswith(".git"):
        path = path[:-4]
    path = path.strip("/")
    if not host or not path:
        raise ToolValidationError("Field 'repo_url' must include host and repository path")
    target = f"{host}/{path.lower()}"
    return {"host": host, "path": path.lower(), "target": target}


def _matches_repo(*, item: GitAccount, repo: dict[str, str] | None) -> bool:
    if repo is None:
        return True
    host = (item.host or "").strip().lower()
    if host != repo["host"]:
        return False
    pattern = (item.scope_pattern or "*").strip().lower() or "*"
    return fnmatch.fnmatch(repo["target"], pattern) or fnmatch.fnmatch(repo["path"], pattern)


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------

def _is_git_write_command(cli_command: str) -> bool:
    tokens = _parse_cli_command(cli_command.strip())
    return _approval_action_for_tokens(tokens) is not None


def _validate_run_command_kind(
    *,
    cli_command: str,
    expect_write: bool,
) -> None:
    is_write = _is_git_write_command(cli_command)
    if expect_write and not is_write:
        raise ToolValidationError("Field 'command' must be 'run_read' for non-write git or gh commands")
    if (not expect_write) and is_write:
        raise ToolValidationError("Field 'command' must be 'run_write' for write git or gh commands")


async def _handle_run(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    session_id = require_session_id(runtime)

    cli_command = payload.get("cli_command")
    if not isinstance(cli_command, str) or not cli_command.strip():
        raise ToolValidationError("Field 'cli_command' must be a non-empty string")

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
    selector = _account_selector_from_payload(payload)

    await _ensure_session_exists(session_id)
    await ensure_runtime_layout(session_id)
    workspace_dir = runtime_workspace_dir(session_id)
    run_dir = _resolve_run_dir(workspace_dir, cwd_raw)

    tokens = _parse_cli_command(cli_command.strip())
    if tokens[0] == "gh":
        return await _execute_gh_command(
            session_id=session_id,
            workspace_dir=workspace_dir,
            run_dir=run_dir,
            tokens=tokens,
            timeout_seconds=timeout_seconds,
            account_selector=selector,
        )

    subcommand, subcommand_index = _extract_git_subcommand(tokens)
    subargs = tokens[subcommand_index + 1 :]

    _validate_no_forbidden_global_flags(tokens[:subcommand_index])
    _validate_no_forbidden_global_flags(subargs)

    network_mode = _network_mode_for_command(subcommand)
    account: _ResolvedAccount | None = None

    if network_mode is not None:
        repo_url, remote_name = _resolve_network_repo_url(subcommand, subargs, run_dir)
        async with AsyncSessionLocal() as db:
            account = await _resolve_git_account(
                db,
                repo_url=repo_url,
                require_write=network_mode == "write",
                selector=selector,
            )
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
        async with AsyncSessionLocal() as db:
            account = await _resolve_git_account(
                db,
                repo_url=origin_url,
                require_write=False,
                selector=selector,
            )
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


async def handle_run_read(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    cli_command = payload.get("cli_command")
    if not isinstance(cli_command, str) or not cli_command.strip():
        raise ToolValidationError("Field 'cli_command' must be a non-empty string")
    _validate_run_command_kind(cli_command=cli_command, expect_write=False)
    return await _handle_run(payload, runtime)


async def handle_run_write(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    cli_command = payload.get("cli_command")
    if not isinstance(cli_command, str) or not cli_command.strip():
        raise ToolValidationError("Field 'cli_command' must be a non-empty string")
    _validate_run_command_kind(cli_command=cli_command, expect_write=True)
    return await _handle_run(payload, runtime)


async def handle_accounts(payload: dict[str, Any]) -> dict[str, Any]:
    host_filter_raw = payload.get("host")
    repo_url_raw = payload.get("repo_url")
    require_write_raw = payload.get("require_write", False)

    if host_filter_raw is not None and (
        not isinstance(host_filter_raw, str) or not host_filter_raw.strip()
    ):
        raise ToolValidationError("Field 'host' must be a non-empty string when provided")
    if repo_url_raw is not None and (
        not isinstance(repo_url_raw, str) or not repo_url_raw.strip()
    ):
        raise ToolValidationError("Field 'repo_url' must be a non-empty string when provided")
    if not isinstance(require_write_raw, bool):
        raise ToolValidationError("Field 'require_write' must be a boolean")

    host_filter = host_filter_raw.strip().lower() if isinstance(host_filter_raw, str) else None
    repo_ref = _parse_accounts_repo_ref(repo_url_raw) if isinstance(repo_url_raw, str) else None

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(GitAccount))
        accounts = result.scalars().all()

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
        matches_repo_flag = _matches_repo(item=item, repo=repo_ref)
        if repo_ref is not None and not matches_repo_flag:
            continue
        if require_write_raw and not (item.token_write or "").strip():
            continue
        if (not require_write_raw) and not (item.token_read or "").strip():
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
                "matches_repo": matches_repo_flag if repo_ref is not None else None,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
        )

    return {
        "total": len(entries),
        "require_write": require_write_raw,
        "repo_target": repo_ref["target"] if repo_ref is not None else None,
        "accounts": entries,
    }


# ---------------------------------------------------------------------------
# Unified tool dispatch
# ---------------------------------------------------------------------------
