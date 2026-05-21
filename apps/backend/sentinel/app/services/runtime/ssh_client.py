from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from shlex import quote
from typing import Any

import asyncssh

from app.schemas.runtime import RuntimeExecResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SSHCredentials:
    host: str
    port: int = 22
    username: str = "lima"
    key_path: Path | None = None
    password: str | None = None


class SSHClient:
    """Small async SSH client for backend-owned runtime commands."""

    def __init__(self, credentials: SSHCredentials) -> None:
        self._credentials = credentials
        self._conn: asyncssh.SSHClientConnection | None = None
        self._lock = asyncio.Lock()

    async def wait_ready(self, *, timeout: int = 60) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        last_exc: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                await self._connect()
                return
            except (OSError, asyncssh.Error) as exc:
                last_exc = exc
                await asyncio.sleep(1)
        raise TimeoutError(
            f"SSH not ready after {timeout}s at "
            f"{self._credentials.host}:{self._credentials.port}: {last_exc}"
        )

    async def run(
        self,
        command: str,
        *,
        timeout: int = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RuntimeExecResult:
        full_command = build_shell_command(command, cwd=cwd, env=env)
        for attempt in (1, 2):
            conn = await self._ensure_conn()
            try:
                result = await asyncio.wait_for(
                    conn.run(full_command, check=False),
                    timeout=timeout,
                )
                return RuntimeExecResult(
                    exit_status=result.exit_status,
                    stdout=result.stdout or "",
                    stderr=result.stderr or "",
                )
            except (
                asyncssh.ConnectionLost,
                asyncssh.DisconnectError,
                ConnectionResetError,
                BrokenPipeError,
                EOFError,
            ) as exc:
                if attempt == 1:
                    logger.debug("SSH connection lost mid-run, reconnecting: %s", exc)
                    await self._reset_conn()
                    continue
                raise
        raise RuntimeError("SSH run exhausted retries")

    async def run_script(
        self,
        script: str,
        *,
        args: list[str] | None = None,
        timeout: int = 300,
    ) -> RuntimeExecResult:
        return await self._run_script(script, args=args or [], timeout=timeout)

    async def create_process(
        self,
        command: str,
        *,
        term_type: str = "xterm-256color",
        term_size: tuple[int, int] = (80, 24),
        encoding: str | None = None,
    ) -> Any:
        conn = await self._ensure_conn()
        return await conn.create_process(
            command,
            term_type=term_type,
            term_size=term_size,
            encoding=encoding,
        )

    async def forward_local_port(
        self,
        listen_host: str,
        listen_port: int,
        target_host: str,
        target_port: int,
    ) -> Any:
        conn = await self._ensure_conn()
        return await conn.forward_local_port(
            listen_host,
            listen_port,
            target_host,
            target_port,
        )

    async def close(self) -> None:
        await self._reset_conn()

    async def _run_script(self, script: str, *, args: list[str], timeout: int) -> RuntimeExecResult:
        conn = await self._ensure_conn()
        argv = " ".join(quote(arg) for arg in args)
        command = f"bash -s -- {argv}" if argv else "bash -s"
        process = await conn.create_process(command, encoding="utf-8")
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        async def _read_stream(stream: Any, chunks: list[str]) -> None:
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                chunks.append(chunk)

        stdout_task = asyncio.create_task(_read_stream(process.stdout, stdout_chunks))
        stderr_task = asyncio.create_task(_read_stream(process.stderr, stderr_chunks))
        try:
            process.stdin.write(script)
            process.stdin.write_eof()
            await asyncio.wait_for(process.wait(), timeout=timeout)
            await asyncio.gather(stdout_task, stderr_task)
        except Exception:
            stdout_task.cancel()
            stderr_task.cancel()
            process.terminate()
            raise

        return RuntimeExecResult(
            exit_status=process.exit_status,
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
        )

    async def _ensure_conn(self) -> asyncssh.SSHClientConnection:
        if self._conn is not None:
            return self._conn
        async with self._lock:
            if self._conn is not None:
                return self._conn
            return await self._connect()

    async def _connect(self) -> asyncssh.SSHClientConnection:
        kwargs: dict[str, Any] = {
            "host": self._credentials.host,
            "port": self._credentials.port,
            "username": self._credentials.username,
            "known_hosts": None,
        }
        if self._credentials.key_path is not None:
            kwargs["client_keys"] = [str(self._credentials.key_path)]
        if self._credentials.password is not None:
            kwargs["password"] = self._credentials.password
        self._conn = await asyncssh.connect(**kwargs)
        return self._conn

    async def _reset_conn(self) -> None:
        async with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def build_shell_command(
    command: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    parts: list[str] = ["set -e"]
    if env:
        for key, value in env.items():
            if not key:
                continue
            parts.append(f"export {key}={quote(str(value))}")
    if cwd:
        parts.append(f"cd {quote(cwd)}")
    parts.append(command)
    return f"bash -lc {quote('; '.join(parts))}"
