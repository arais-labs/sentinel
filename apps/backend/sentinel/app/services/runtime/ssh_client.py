from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

import asyncssh

logger = logging.getLogger(__name__)


@dataclass
class SSHExecResult:
    exit_status: int | None
    stdout: str
    stderr: str


class SSHClient:
    """Async SSH client for executing commands on a remote machine."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        key_path: Path | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._key_path = key_path
        self._conn: asyncssh.SSHClientConnection | None = None
        self._lock = asyncio.Lock()

    async def wait_ready(self, *, timeout: int = 60) -> None:
        """Poll SSH until the host is reachable."""
        deadline = asyncio.get_event_loop().time() + timeout
        last_exc: Exception | None = None
        while asyncio.get_event_loop().time() < deadline:
            try:
                connect_kwargs: dict = {
                    "host": self._host,
                    "port": self._port,
                    "username": self._username,
                    "known_hosts": None,
                    "login_timeout": 30,
                }
                if self._key_path:
                    connect_kwargs["client_keys"] = [str(self._key_path)]
                conn = await asyncssh.connect(**connect_kwargs)
                self._conn = conn
                logger.info("SSH ready at %s:%d", self._host, self._port)
                return
            except (OSError, asyncssh.Error) as exc:
                last_exc = exc
                await asyncio.sleep(1)
        raise TimeoutError(
            f"SSH not ready after {timeout}s at {self._host}:{self._port}: {last_exc}"
        )

    async def _ensure_conn(self) -> asyncssh.SSHClientConnection:
        if self._conn is not None:
            return self._conn
        async with self._lock:
            if self._conn is not None:
                return self._conn
            connect_kwargs: dict = {
                "host": self._host,
                "port": self._port,
                "username": self._username,
                "known_hosts": None,
            }
            if self._key_path:
                connect_kwargs["client_keys"] = [str(self._key_path)]
            self._conn = await asyncssh.connect(**connect_kwargs)
            return self._conn

    async def run(
        self,
        command: str,
        *,
        timeout: int = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SSHExecResult:
        conn = await self._ensure_conn()

        parts: list[str] = []
        if env:
            for k, v in env.items():
                parts.append(f"export {k}={_shell_quote(v)};")
        if cwd:
            parts.append(f"cd {_shell_quote(cwd)} &&")
        parts.append(command)
        full_command = " ".join(parts)

        result = await asyncio.wait_for(
            conn.run(full_command, check=False),
            timeout=timeout,
        )

        return SSHExecResult(
            exit_status=result.exit_status,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )

    async def run_detached(
        self,
        command: str,
        *,
        stdout_path: str,
        stderr_path: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int:
        """Start a background command, return its PID."""
        conn = await self._ensure_conn()
        parts: list[str] = []
        if env:
            for k, v in env.items():
                parts.append(f"export {k}={_shell_quote(v)};")
        if cwd:
            parts.append(f"cd {_shell_quote(cwd)} &&")
        parts.append(
            f"nohup {command} > {_shell_quote(stdout_path)} 2> {_shell_quote(stderr_path)} & echo $!"
        )
        full_command = " ".join(parts)

        result = await conn.run(full_command, check=False)
        pid_str = (result.stdout or "").strip().split("\n")[-1]
        return int(pid_str)

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _shell_quote(s: str) -> str:
    """POSIX shell single-quoting."""
    return "'" + s.replace("'", "'\\''") + "'"
