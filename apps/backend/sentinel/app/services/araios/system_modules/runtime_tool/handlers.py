"""Native module: runtime — shell command execution in the runtime container."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import UUID, uuid4

from app.database.database import AsyncSessionLocal
from app.services.runtime import get_runtime
from app.services.runtime.base import RuntimeExecResult
from app.services.runtime.session_runtime import (
    ensure_runtime_layout,
    finalize_detached_runtime_job,
    get_detached_runtime_job,
    list_detached_runtime_jobs,
    mark_runtime_state,
    read_detached_runtime_job_logs,
    register_detached_runtime_job,
    runtime_logs_dir,
    runtime_workspace_dir,
    stop_detached_runtime_job,
)
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.runtime_context import require_runtime_session_id

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_RUNTIME_EXEC_OUTPUT_CHARS = 50_000
_RUNTIME_EXEC_STREAM_READ_BYTES = 65_536
_RUNTIME_EXEC_STREAM_DRAIN_SECONDS = 2.0
_RUNTIME_EXEC_TIMEOUT_KILL_WAIT_SECONDS = 3.0
_RUNTIME_EXEC_AUTO_PROMOTE_SECONDS = 30
# ---------------------------------------------------------------------------
# Internal helpers (moved from builtin.py)
# ---------------------------------------------------------------------------


def _ssh_shell_quote(s: str) -> str:
    """POSIX single-quoting for SSH commands."""
    return "'" + s.replace("'", "'\\''") + "'"


def _truncate_runtime_exec_text(value: str | None) -> str:
    text = value or ""
    if len(text) <= _MAX_RUNTIME_EXEC_OUTPUT_CHARS:
        return text
    return f"{text[:_MAX_RUNTIME_EXEC_OUTPUT_CHARS]}\n...[truncated]"


def _command_requests_background_execution(command: str) -> bool:
    normalized = command.strip().lower()
    if not normalized:
        return False
    if re.search(r"\b(?:nohup|disown)\b", normalized):
        return True
    for index, char in enumerate(normalized):
        if char != "&":
            continue
        prev_char = normalized[index - 1] if index > 0 else ""
        next_char = normalized[index + 1] if index + 1 < len(normalized) else ""
        if prev_char == "&" or next_char == "&":
            continue
        # Allow file-descriptor duplication like 2>&1 or >&2.
        if prev_char == ">":
            continue
        return True
    return False


def _detached_job_has_terminal_result(job: dict[str, Any]) -> bool:
    status = str(job.get("status") or "")
    if status == "cancelled":
        return True
    if status not in {"completed", "failed"}:
        return False
    return isinstance(job.get("returncode"), int)


async def _drain_runtime_exec_streams(
    proc: asyncio.subprocess.Process,
) -> tuple[bytes, bytes]:
    stdout = await _drain_runtime_exec_stream(proc.stdout)
    stderr = await _drain_runtime_exec_stream(proc.stderr)
    return stdout, stderr


async def _drain_runtime_exec_stream(
    stream: asyncio.StreamReader | None,
) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    remaining = _RUNTIME_EXEC_STREAM_READ_BYTES
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _RUNTIME_EXEC_STREAM_DRAIN_SECONDS
    while remaining > 0:
        remaining_timeout = deadline - loop.time()
        if remaining_timeout <= 0:
            break
        chunk_size = min(remaining, 8_192)
        try:
            chunk = await asyncio.wait_for(stream.read(chunk_size), timeout=remaining_timeout)
        except TimeoutError:
            break
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _watch_detached_runtime_process(
    *,
    proc: asyncio.subprocess.Process,
    session_id: UUID,
    job_id: str,
) -> None:
    async def _watch() -> None:
        try:
            returncode = await proc.wait()
            await finalize_detached_runtime_job(
                session_id,
                job_id=job_id,
                returncode=returncode,
            )
        except Exception as exc:  # noqa: BLE001
            await finalize_detached_runtime_job(
                session_id,
                job_id=job_id,
                returncode=None,
                error=str(exc),
            )

    asyncio.create_task(_watch())


async def _start_detached_runtime_job_via_ssh(
    *,
    runtime,
    session_id: UUID,
    command_text: str,
    privilege: str,
    sandbox_workspace: str,
    sandbox_cwd: str,
    env: dict[str, str],
) -> dict[str, Any]:
    logs_dir_vm = f"{sandbox_workspace}/.runtime/logs"
    log_token = uuid4().hex[:10]
    stdout_vm_path = f"{logs_dir_vm}/{log_token}.stdout.log"
    stderr_vm_path = f"{logs_dir_vm}/{log_token}.stderr.log"
    exitcode_vm_path = f"{logs_dir_vm}/{log_token}.exitcode"

    await runtime.client.run(
        f"mkdir -p {logs_dir_vm}",
        timeout=10,
        cwd=sandbox_cwd,
        env=env,
        as_root=(privilege == "root"),
    )

    detached_command = (
        f"{command_text}; "
        f"code=$?; "
        f"printf '%s' \"$code\" > {_ssh_shell_quote(exitcode_vm_path)}; "
        f"exit \"$code\""
    )

    try:
        pid = await runtime.client.run_detached(
            detached_command,
            stdout_path=stdout_vm_path,
            stderr_path=stderr_vm_path,
            cwd=sandbox_cwd,
            env=env,
            as_root=(privilege == "root"),
        )
    except TimeoutError as exc:
        raise RuntimeError("Detached runtime launcher timed out before returning a pid") from exc

    host_logs_dir = runtime_logs_dir(session_id)
    host_logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_host = host_logs_dir / f"{log_token}.stdout.log"
    stderr_host = host_logs_dir / f"{log_token}.stderr.log"
    exitcode_host = host_logs_dir / f"{log_token}.exitcode"

    job = await register_detached_runtime_job(
        session_id,
        command=command_text,
        cwd=sandbox_cwd,
        pid=pid,
        stdout_path=stdout_vm_path,
        stderr_path=stderr_vm_path,
        host_stdout_path=stdout_host,
        host_stderr_path=stderr_host,
        exitcode_path=exitcode_vm_path,
        host_exitcode_path=exitcode_host,
    )
    return {
        "job": job,
        "pid": pid,
        "stdout_vm_path": stdout_vm_path,
        "stderr_vm_path": stderr_vm_path,
        "exitcode_vm_path": exitcode_vm_path,
    }


async def _wait_for_detached_runtime_job_completion(
    *,
    session_id: UUID,
    job_id: str,
    wait_seconds: int,
) -> dict[str, Any] | None:
    deadline = asyncio.get_running_loop().time() + max(1, wait_seconds)
    while True:
        job = await get_detached_runtime_job(session_id, job_id=job_id)
        if job is None:
            return None
        if _detached_job_has_terminal_result(job):
            logs = await read_detached_runtime_job_logs(
                session_id,
                job_id=job_id,
                tail_bytes=_MAX_RUNTIME_EXEC_OUTPUT_CHARS,
            )
            return {
                "job": job,
                "logs": logs,
            }
        if asyncio.get_running_loop().time() >= deadline:
            return None
        await asyncio.sleep(0.5)


async def _read_remote_runtime_log_tail(
    runtime,
    *,
    path: str,
    tail_bytes: int,
) -> str:
    quoted_path = _ssh_shell_quote(path)
    script = f"test -f {quoted_path} && tail -c {int(tail_bytes)} {quoted_path} || true"
    result = await runtime.client.run(
        script,
        timeout=10,
    )
    return result.stdout or ""


async def _ensure_session_exists(session_id: UUID) -> None:
    from sqlalchemy import select as sa_select
    from app.models import Session

    async with AsyncSessionLocal() as db:
        result = await db.execute(sa_select(Session).where(Session.id == session_id))
        session = result.scalars().first()
        if session is None:
            raise ToolValidationError("Session not found")


async def _execute_in_runtime(
    *,
    session_id: UUID,
    command_text: str,
    privilege: str,
    workspace_dir: Path,
    cwd_raw: str | None,
    env_payload: dict[str, Any],
    timeout_seconds: int,
    detached: bool,
    auto_promote: bool,
) -> dict[str, Any]:
    """Execute a command in the runtime via the provider-neutral runtime client."""
    runtime = await get_runtime().ensure(session_id)

    sandbox_workspace = runtime.workspace_path

    # Resolve cwd
    sandbox_cwd = sandbox_workspace
    if isinstance(cwd_raw, str) and cwd_raw.strip():
        requested = cwd_raw.strip()
        if Path(requested).is_absolute():
            # Absolute path — must be under the remote workspace
            if not requested.startswith(sandbox_workspace):
                raise ToolValidationError(f"Field 'cwd' must stay within session workspace ({sandbox_workspace})")
            sandbox_cwd = requested
        else:
            sandbox_cwd = f"{sandbox_workspace}/{requested}"

    # Build env for the remote runtime
    env: dict[str, str] = {
        "HOME": sandbox_workspace,
        "PWD": sandbox_cwd,
        "TMPDIR": "/tmp",
    }
    for key, value in env_payload.items():
        if not isinstance(key, str) or not key.strip():
            raise ToolValidationError("Environment variable keys must be non-empty strings")
        if value is None:
            env.pop(key, None)
            continue
        if not isinstance(value, (str, int, float, bool)):
            raise ToolValidationError(
                f"Environment variable '{key}' must be string/number/boolean/null"
            )
        env[key] = str(value)

    command_result_details: dict[str, Any] | None = None
    await mark_runtime_state(session_id, active=True, command=command_text, pid=None)

    try:
        if detached:
            detached_data = await _start_detached_runtime_job_via_ssh(
                runtime=runtime,
                session_id=session_id,
                command_text=command_text,
                privilege=privilege,
                sandbox_workspace=sandbox_workspace,
                sandbox_cwd=sandbox_cwd,
                env=env,
            )
            job = detached_data["job"]

            await mark_runtime_state(session_id, active=False, command=command_text, pid=detached_data["pid"])
            return {
                "ok": True,
                "detached": True,
                "job": job,
                "session_id": str(session_id),
                "workspace": sandbox_workspace,
                "cwd": sandbox_cwd,
                "privilege": privilege,
            }

        if auto_promote:
            detached_data = await _start_detached_runtime_job_via_ssh(
                runtime=runtime,
                session_id=session_id,
                command_text=command_text,
                privilege=privilege,
                sandbox_workspace=sandbox_workspace,
                sandbox_cwd=sandbox_cwd,
                env=env,
            )
            completed = await _wait_for_detached_runtime_job_completion(
                session_id=session_id,
                job_id=str(detached_data["job"]["id"]),
                wait_seconds=_RUNTIME_EXEC_AUTO_PROMOTE_SECONDS,
            )
            if completed is None:
                command_result_details = {
                    "ok": True,
                    "detached": True,
                    "auto_promoted": True,
                    "preemptively_backgrounded": True,
                    "background_reason": "requested_timeout_exceeds_interactive_threshold",
                    "message": (
                        "Started as a tracked background job because the requested "
                        f"timeout ({timeout_seconds}s) exceeds the interactive "
                        f"threshold ({_RUNTIME_EXEC_AUTO_PROMOTE_SECONDS}s)."
                    ),
                    "job": detached_data["job"],
                    "privilege": privilege,
                }
                await mark_runtime_state(session_id, active=False, command=command_text, pid=detached_data["pid"])
                return {
                    **command_result_details,
                    "session_id": str(session_id),
                    "workspace": sandbox_workspace,
                    "cwd": sandbox_cwd,
                }

            logs = completed.get("logs") or {}
            job = completed["job"]
            if not _detached_job_has_terminal_result(job):
                command_result_details = {
                    "ok": True,
                    "detached": True,
                    "auto_promoted": True,
                    "preemptively_backgrounded": True,
                    "background_reason": "requested_timeout_exceeds_interactive_threshold",
                    "message": (
                        "Started as a tracked background job because the requested "
                        f"timeout ({timeout_seconds}s) exceeds the interactive "
                        f"threshold ({_RUNTIME_EXEC_AUTO_PROMOTE_SECONDS}s)."
                    ),
                    "job": job,
                    "privilege": privilege,
                }
                await mark_runtime_state(session_id, active=False, command=command_text, pid=detached_data["pid"])
                return {
                    **command_result_details,
                    "session_id": str(session_id),
                    "workspace": sandbox_workspace,
                    "cwd": sandbox_cwd,
                }
            stdout_text = _truncate_runtime_exec_text(str(logs.get("stdout_tail") or ""))
            stderr_text = _truncate_runtime_exec_text(str(logs.get("stderr_tail") or ""))
            if not stdout_text:
                stdout_text = _truncate_runtime_exec_text(
                    await _read_remote_runtime_log_tail(
                        runtime,
                        path=str(detached_data["stdout_vm_path"]),
                        tail_bytes=_MAX_RUNTIME_EXEC_OUTPUT_CHARS,
                    )
                )
            if not stderr_text:
                stderr_text = _truncate_runtime_exec_text(
                    await _read_remote_runtime_log_tail(
                        runtime,
                        path=str(detached_data["stderr_vm_path"]),
                        tail_bytes=_MAX_RUNTIME_EXEC_OUTPUT_CHARS,
                    )
                )
            ok = str(job.get("status")) == "completed"
            command_result_details = {
                "ok": ok,
                "detached": False,
                "auto_promoted": True,
                "preemptively_backgrounded": True,
                "background_reason": "requested_timeout_exceeds_interactive_threshold",
                "timed_out": False,
                "returncode": job.get("returncode"),
                "stdout": stdout_text,
                "stderr": stderr_text,
                "message": None,
                "privilege": privilege,
            }
            await mark_runtime_state(session_id, active=False, command=command_text, pid=None)
            return {
                **command_result_details,
                "session_id": str(session_id),
                "workspace": sandbox_workspace,
                "cwd": sandbox_cwd,
            }

        # Inline execution
        timeout_hint: str | None = None
        timed_out = False
        try:
            result = await runtime.client.run(
                command_text,
                cwd=sandbox_cwd,
                env=env,
                timeout=timeout_seconds,
                as_root=(privilege == "root"),
            )
        except TimeoutError:
            timed_out = True
            timeout_hint = (
                f"Command timed out after {timeout_seconds}s. "
                "Use detached=true for long-running/background commands."
            )
            result = RuntimeExecResult(exit_status=-1, stdout="", stderr="[timed out]")

        stdout_text = _truncate_runtime_exec_text(result.stdout)
        stderr_text = _truncate_runtime_exec_text(result.stderr)
        ok = not timed_out and result.exit_status == 0

        command_result_details = {
            "ok": ok,
            "timed_out": timed_out,
            "returncode": result.exit_status,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "message": timeout_hint,
            "privilege": privilege,
        }
        return {
            **command_result_details,
            "session_id": str(session_id),
            "workspace": sandbox_workspace,
            "cwd": sandbox_cwd,
        }
    finally:
        await mark_runtime_state(
            session_id,
            active=False,
            command=command_text,
            pid=None,
            action_details=command_result_details,
        )


# ---------------------------------------------------------------------------
# Handler functions (module-level)
# ---------------------------------------------------------------------------


async def _handle_run_with_privilege(
    payload: dict[str, Any],
    *,
    runtime: ToolRuntimeContext,
    privilege: str,
) -> dict[str, Any]:
    session_id = require_runtime_session_id(runtime)

    shell_command = payload.get("shell_command")
    if not isinstance(shell_command, str) or not shell_command.strip():
        raise ToolValidationError("Field 'shell_command' must be a non-empty string")
    command_text = shell_command.strip()

    timeout_seconds = payload.get("timeout_seconds", 300)
    timeout_seconds_explicit = "timeout_seconds" in payload
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
    ):
        raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")
    timeout_seconds = min(timeout_seconds, 1800)

    detached = payload.get("detached", False)
    if not isinstance(detached, bool):
        raise ToolValidationError("Field 'detached' must be a boolean")
    if not detached and _command_requests_background_execution(command_text):
        raise ToolValidationError(
            "Background shell execution is not allowed for inline runtime commands. "
            "Use detached=true for long-running/background commands."
        )

    cwd_raw = payload.get("cwd")
    if cwd_raw is not None and (not isinstance(cwd_raw, str) or not cwd_raw.strip()):
        raise ToolValidationError("Field 'cwd' must be a non-empty string when provided")

    env_payload = payload.get("env", {})
    if env_payload is None:
        env_payload = {}
    if not isinstance(env_payload, dict):
        raise ToolValidationError("Field 'env' must be an object")

    await _ensure_session_exists(session_id)
    await ensure_runtime_layout(session_id)
    workspace_dir = runtime_workspace_dir(session_id)

    return await _execute_in_runtime(
        session_id=session_id,
        command_text=command_text,
        privilege=privilege,
        workspace_dir=workspace_dir,
        cwd_raw=cwd_raw,
        env_payload=env_payload,
        timeout_seconds=timeout_seconds,
        detached=detached,
        auto_promote=timeout_seconds_explicit and timeout_seconds > _RUNTIME_EXEC_AUTO_PROMOTE_SECONDS,
    )


async def handle_run_user(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    return await _handle_run_with_privilege(payload, runtime=runtime, privilege="user")


async def handle_run_root(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    return await _handle_run_with_privilege(payload, runtime=runtime, privilege="root")


async def handle_jobs_list(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    session_id = require_runtime_session_id(runtime)
    include_completed = payload.get("include_completed", True)
    if not isinstance(include_completed, bool):
        raise ToolValidationError("Field 'include_completed' must be a boolean")
    await _ensure_session_exists(session_id)
    jobs = await list_detached_runtime_jobs(session_id, include_completed=include_completed)
    return {
        "session_id": str(session_id),
        "jobs": jobs,
        "total": len(jobs),
    }


async def handle_job_status(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ToolValidationError("Field 'job_id' must be a non-empty string")
    session_id = require_runtime_session_id(runtime)
    await _ensure_session_exists(session_id)
    job = await get_detached_runtime_job(session_id, job_id=job_id.strip())
    if job is None:
        raise ToolValidationError("Detached runtime job not found")
    return {"session_id": str(session_id), "job": job}


async def handle_job_logs(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ToolValidationError("Field 'job_id' must be a non-empty string")
    session_id = require_runtime_session_id(runtime)
    tail_bytes = payload.get("tail_bytes", 8000)
    if (
        not isinstance(tail_bytes, int)
        or isinstance(tail_bytes, bool)
        or tail_bytes < 256
    ):
        raise ToolValidationError("Field 'tail_bytes' must be an integer >= 256")
    await _ensure_session_exists(session_id)
    data = await read_detached_runtime_job_logs(
        session_id,
        job_id=job_id.strip(),
        tail_bytes=tail_bytes,
    )
    if data is None:
        raise ToolValidationError("Detached runtime job not found")
    return {"session_id": str(session_id), **data}


async def handle_job_stop(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ToolValidationError("Field 'job_id' must be a non-empty string")
    session_id = require_runtime_session_id(runtime)
    force = payload.get("force", False)
    if not isinstance(force, bool):
        raise ToolValidationError("Field 'force' must be a boolean")
    await _ensure_session_exists(session_id)
    job = await stop_detached_runtime_job(
        session_id,
        job_id=job_id.strip(),
        force=force,
        reason="Stopped by runtime command=job_stop",
    )
    if job is None:
        raise ToolValidationError("Detached runtime job not found")
    return {"session_id": str(session_id), "job": job}
