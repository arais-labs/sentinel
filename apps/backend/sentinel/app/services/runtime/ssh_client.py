from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import asyncssh

from app.services.runtime.base import RuntimeExecResult

logger = logging.getLogger(__name__)


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
        as_root: bool = False,
    ) -> RuntimeExecResult:
        full_command = _build_shell_command(
            command,
            cwd=cwd,
            env=env,
            as_root=as_root,
        )
        # The cached connection can go stale silently — the SSH server inside
        # the guest VM idle-times-out long-lived sessions and asyncssh only
        # surfaces it on the next operation. Retry once with a fresh conn so
        # callers don't see spurious "guest unreachable" errors after a
        # period of inactivity (page refresh, agent pause, etc.).
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
                    logger.warning(
                        "SSH connection lost mid-run, reconnecting: %s", exc
                    )
                    await self._reset_conn()
                    continue
                raise
        # Unreachable — the loop either returns or raises, but keep mypy happy.
        raise RuntimeError("SSH run exhausted retries")

    async def _reset_conn(self) -> None:
        async with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    async def run_detached(
        self,
        command: str,
        *,
        stdout_path: str,
        stderr_path: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> int:
        """Start a background command, return its PID."""
        inner_script = _build_inline_script(command, cwd=cwd, env=env)
        shell_prefix = "sudo bash -lc" if as_root else "bash -lc"
        return await self.run_detached_script(
            inner_script,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            shell_prefix=shell_prefix,
        )

    async def run_detached_script(
        self,
        script: str,
        *,
        stdout_path: str,
        stderr_path: str,
        shell_prefix: str = "bash -lc",
    ) -> int:
        """Start a background shell script under the requested shell and return its PID."""
        conn = await self._ensure_conn()
        redirected_script = (
            f"exec > {_shell_quote(stdout_path)} 2> {_shell_quote(stderr_path)}; "
            f"{script}"
        )
        full_command = (
            f"setsid nohup {shell_prefix} {_shell_quote(redirected_script)} "
            f"</dev/null >/dev/null 2>&1 & echo $!"
        )

        process = await conn.create_process(full_command)
        try:
            pid_line = await asyncio.wait_for(process.stdout.readline(), timeout=5)
        finally:
            process.channel.close()
        pid_str = (pid_line or "").strip()
        return int(pid_str)

    async def create_process(
        self,
        command: str,
        *,
        term_type: str = "xterm-256color",
        term_size: tuple[int, int] = (80, 24),
        encoding: str | None = None,
    ):
        conn = await self._ensure_conn()
        return await conn.create_process(
            command,
            term_type=term_type,
            term_size=term_size,
            encoding=encoding,
        )

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _shell_quote(s: str) -> str:
    """POSIX shell single-quoting."""
    return "'" + s.replace("'", "'\\''") + "'"


def _build_inline_script(
    command: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    parts: list[str] = []
    if env:
        for key, value in env.items():
            parts.append(f"export {key}={_shell_quote(value)};")
    if cwd:
        parts.append(f"cd {_shell_quote(cwd)} &&")
    parts.append(command)
    return " ".join(parts)


def _build_shell_command(
    command: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    as_root: bool = False,
) -> str:
    script = _build_inline_script(command, cwd=cwd, env=env)
    if as_root:
        return f"sudo bash -lc {_shell_quote(script)}"
    return f"bash -lc {_shell_quote(script)}"
