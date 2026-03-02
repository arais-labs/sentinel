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
from app.services.tools.registry import ToolDefinition

_MAX_GIT_OUTPUT_CHARS = 50_000
_FORBIDDEN_GIT_GLOBAL_FLAGS = {"-c", "-C", "--git-dir", "--work-tree"}
_NETWORK_READ_SUBCOMMANDS = {"clone", "fetch", "pull", "ls-remote", "submodule"}
_NETWORK_WRITE_SUBCOMMANDS = {"push"}
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
        approval_timeout_seconds = min(approval_timeout_seconds, _MAX_PUSH_APPROVAL_TIMEOUT_SECONDS)

        cwd_raw = payload.get("cwd")
        if cwd_raw is not None and (not isinstance(cwd_raw, str) or not cwd_raw.strip()):
            raise ToolValidationError("Field 'cwd' must be a non-empty string when provided")

        await _ensure_session_exists(session_factory, session_id)
        await ensure_runtime_layout(session_id)
        workspace_dir = runtime_workspace_dir(session_id)
        run_dir = _resolve_run_dir(workspace_dir, cwd_raw)

        tokens = _parse_git_command(command.strip())
        subcommand, subcommand_index = _extract_git_subcommand(tokens)
        subargs = tokens[subcommand_index + 1 :]

        _validate_no_forbidden_global_flags(tokens[:subcommand_index])
        _validate_no_forbidden_global_flags(subargs)

        network_mode = _network_mode_for_command(subcommand)
        approval: dict[str, Any] | None = None
        account: _ResolvedAccount | None = None

        if network_mode is not None:
            repo_url, remote_name = _resolve_network_repo_url(subcommand, subargs, run_dir)
            async with session_factory() as db:
                account = await _resolve_git_account(db, repo_url=repo_url, require_write=network_mode == "write")

            if network_mode == "write":
                assert account is not None
                approval_row = await _create_push_approval(
                    session_factory=session_factory,
                    account_id=account.account.id,
                    session_id=session_id,
                    repo_url=repo_url,
                    remote_name=remote_name,
                    command=command.strip(),
                    requested_by=f"session:{session_id}",
                    timeout_seconds=approval_timeout_seconds,
                )
                try:
                    decision = await _wait_for_push_approval(
                        session_factory=session_factory,
                        approval_id=approval_row.id,
                        timeout_seconds=approval_timeout_seconds,
                    )
                except asyncio.CancelledError:
                    await _cancel_push_approval(
                        session_factory=session_factory,
                        approval_id=approval_row.id,
                        note="Cancelled by user while awaiting approval",
                    )
                    raise
                approval = {
                    "id": str(approval_row.id),
                    "status": decision.status,
                    "decision_by": decision.decision_by,
                    "decision_note": decision.decision_note,
                }
                if decision.status != "approved":
                    raise ToolExecutionError(
                        f"Push approval {decision.status}. "
                        "Open Sentinel Git tab to approve/reject pending push requests."
                    )

            result = await _run_network_git(
                account=account,
                workspace_dir=workspace_dir,
                run_dir=run_dir,
                tokens=tokens,
                timeout_seconds=timeout_seconds,
            )
            if approval is not None:
                result["approval"] = approval
                await _record_push_result(
                    session_factory=session_factory,
                    approval_id=UUID(approval["id"]),
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
                raise ToolValidationError("No git account matches this repository for commit attribution")
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
            "Execute git commands inside the per-session workspace with managed credentials. "
            "Network git operations are intercepted and account-matched automatically."
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
                    "description": "Git command, e.g. 'git clone ...', 'git status', 'git push'",
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
    )


def _parse_git_command(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ToolValidationError(f"Invalid command syntax: {exc}") from exc
    if not tokens:
        raise ToolValidationError("Command is empty")
    if tokens[0] != "git":
        raise ToolValidationError("Only git commands are allowed")
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

    remote_name = _first_positional_argument(subargs) or "origin"
    repo_url = _resolve_origin_url(run_dir, remote_name=remote_name)
    return repo_url, remote_name


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
    }
    idx = 0
    while idx < len(args):
        part = args[idx]
        if part == "--":
            return args[idx + 1] if idx + 1 < len(args) else None
        if part in options_with_value:
            idx += 2
            continue
        if part.startswith("--") and "=" in part:
            idx += 1
            continue
        if part.startswith("-"):
            idx += 1
            continue
        return part
    return None


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
        raise ToolValidationError("Unable to resolve repository remote URL for account matching")
    repo_url = (result["stdout"] or "").strip()
    if not repo_url:
        raise ToolValidationError("Repository remote URL is empty")
    return repo_url


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
