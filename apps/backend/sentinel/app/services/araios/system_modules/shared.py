"""Shared utilities for system module handlers.

Extracted from the old builtin.py — used by multiple native tool modules.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import shlex
import signal
import socket
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Session
from app.services.runtime import get_runtime
from app.services.runtime.ssh_client import SSHExecResult
from app.services.runtime.session_runtime import (
    register_detached_runtime_job,
    runtime_logs_dir,
    runtime_workspace_dir,
)
from app.services.tools.executor import ToolValidationError

_MAX_RUNTIME_EXEC_OUTPUT_CHARS = 50_000
_RUNTIME_EXEC_STREAM_READ_BYTES = 65_536
_RUNTIME_EXEC_STREAM_DRAIN_SECONDS = 2.0
_RUNTIME_EXEC_TIMEOUT_KILL_WAIT_SECONDS = 3.0
_RUNTIME_BACKGROUND_AMPERSAND_RE = re.compile(r"(?<!&)&(?!&)")


async def ensure_session_exists(
    session_factory: async_sessionmaker[AsyncSession], session_id: UUID
) -> None:
    async with session_factory() as db:
        result = await db.execute(select(Session).where(Session.id == session_id))
        if result.scalars().first() is None:
            raise ToolValidationError("Session not found")


def ssh_shell_quote(s: str) -> str:
    """Shell-quote for passing through SSH."""
    return shlex.quote(s)


def truncate_runtime_exec_text(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= _MAX_RUNTIME_EXEC_OUTPUT_CHARS:
        return value
    half = _MAX_RUNTIME_EXEC_OUTPUT_CHARS // 2
    return (
        value[:half]
        + f"\n\n... [{len(value) - _MAX_RUNTIME_EXEC_OUTPUT_CHARS} chars truncated] ...\n\n"
        + value[-half:]
    )


def command_requests_background_execution(command: str) -> bool:
    """Check if a shell command uses & for background execution."""
    stripped = command.strip()
    if stripped.endswith("&") and not stripped.endswith("&&"):
        return True
    return bool(_RUNTIME_BACKGROUND_AMPERSAND_RE.search(stripped))


async def validate_public_hostname(hostname: str) -> None:
    """Raise ToolValidationError if hostname resolves to a private/reserved IP."""
    allowed_hosts_raw = os.environ.get("SSRF_ALLOW_HOSTS", "")
    if allowed_hosts_raw:
        allowed = {h.strip().lower() for h in allowed_hosts_raw.split(",") if h.strip()}
        if hostname.strip().lower() in allowed:
            return
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(hostname, None)
    except socket.gaierror:
        return
    for family, _type, _proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if addr.is_private or addr.is_reserved or addr.is_loopback or addr.is_link_local:
            raise ToolValidationError(
                f"Blocked: {hostname} resolves to private/reserved address {ip_str}"
            )


async def execute_via_ssh(
    *,
    session_id: UUID,
    command_text: str,
    privilege: str = "user",
    workspace_dir: str | None = None,
    cwd_raw: str | None = None,
    env_payload: dict[str, str] | None = None,
    timeout_seconds: int = 300,
    detached: bool = False,
) -> dict[str, Any]:
    """Execute a command in the session's runtime container via SSH."""
    rt = get_runtime()
    ssh = await rt.ssh(str(session_id))

    ws_dir = workspace_dir or runtime_workspace_dir(session_id)
    cwd = ws_dir
    if cwd_raw:
        candidate = os.path.normpath(os.path.join(ws_dir, cwd_raw.strip()))
        if not candidate.startswith(ws_dir):
            raise ToolValidationError("Field 'cwd' must stay within session workspace")
        cwd = candidate

    env_parts: list[str] = []
    if env_payload:
        for k, v in env_payload.items():
            env_parts.append(f"{ssh_shell_quote(str(k))}={ssh_shell_quote(str(v))}")

    full_command = command_text
    if env_parts:
        full_command = " ".join(env_parts) + " " + full_command

    if detached:
        job_id = await _launch_detached(
            ssh=ssh,
            session_id=session_id,
            command=full_command,
            cwd=cwd,
            privilege=privilege,
        )
        return {
            "ok": True,
            "detached": True,
            "job_id": job_id,
            "message": f"Background job started: {job_id}",
            "privilege": privilege,
            "workspace": ws_dir,
            "cwd": cwd,
        }

    try:
        result: SSHExecResult = await asyncio.wait_for(
            ssh.run(
                full_command,
                cwd=cwd,
                user="root" if privilege == "root" else None,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "timed_out": True,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command timed out after {timeout_seconds}s",
            "message": None,
            "privilege": privilege,
            "workspace": ws_dir,
            "cwd": cwd,
        }

    stdout = truncate_runtime_exec_text(result.stdout)
    stderr = truncate_runtime_exec_text(result.stderr)

    return {
        "ok": result.returncode == 0,
        "timed_out": False,
        "returncode": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "message": None,
        "privilege": privilege,
        "workspace": ws_dir,
        "cwd": cwd,
    }


async def _launch_detached(
    *,
    ssh: Any,
    session_id: UUID,
    command: str,
    cwd: str,
    privilege: str,
) -> str:
    """Launch a detached background job and return its job_id."""
    from uuid import uuid4

    job_id = uuid4().hex[:12]
    logs_dir = runtime_logs_dir(session_id)

    stdout_log = f"{logs_dir}/{job_id}.stdout"
    stderr_log = f"{logs_dir}/{job_id}.stderr"
    pid_file = f"{logs_dir}/{job_id}.pid"

    wrapper = (
        f"mkdir -p {ssh_shell_quote(logs_dir)} && "
        f"nohup sh -c {ssh_shell_quote(command)} "
        f"> {ssh_shell_quote(stdout_log)} "
        f"2> {ssh_shell_quote(stderr_log)} & "
        f"echo $! > {ssh_shell_quote(pid_file)}"
    )

    await ssh.run(wrapper, cwd=cwd, user="root" if privilege == "root" else None)

    await register_detached_runtime_job(
        session_id=session_id,
        job_id=job_id,
        command=command,
        cwd=cwd,
        privilege=privilege,
    )

    return job_id
