from __future__ import annotations

import asyncio
import os
import pwd
import signal
from typing import Any, Protocol

from app.schemas.runtime import RuntimeExecResult
from app.services.runtime.ssh_client import build_shell_command

# Per-line read buffer for the terminal stream (asyncio default is 64 KiB).
_TERMINAL_STREAM_LIMIT = 4 * 1024 * 1024

_LOGIN_ENV_MARKER = "__SENTINEL_LOGIN_ENV__"


async def _capture_login_environment() -> dict[str, str]:
    """Resolve the desktop user's login-shell environment — the local equivalent
    of SSH-ing into this machine.

    ``env -i`` drops everything the backend inherited (so no secrets ever reach a
    command); the user's login shell rebuilds the session from their Mac profile.
    Identity comes from the OS account database, and the backend PATH is seeded so
    path_helper keeps the bundled tools as a fallback at the tail. The env is
    dumped after a marker so any profile stdout is discarded — commands then run
    in a non-login shell with this env, never re-sourcing the profile.
    """
    account = pwd.getpwuid(os.getuid())
    shell = account.pw_shell or "/bin/zsh"
    seed = {
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "SHELL": shell,
    }
    bundled_path = os.environ.get("PATH")
    if bundled_path:
        seed["PATH"] = bundled_path
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/env",
        "-i",
        *[f"{key}={value}" for key, value in seed.items()],
        shell,
        "-l",
        "-c",
        f'printf "%s\\n" "{_LOGIN_ENV_MARKER}"; /usr/bin/env',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
    except (TimeoutError, asyncio.TimeoutError):
        process.kill()
        await process.wait()
        return dict(seed)
    text = stdout.decode("utf-8", errors="replace")
    _, separator, dump = text.partition(f"{_LOGIN_ENV_MARKER}\n")
    env = dict(seed)
    if separator:
        for line in dump.splitlines():
            key, eq, value = line.partition("=")
            if eq and key:
                env[key] = value
    return env


def _kill_group(process: asyncio.subprocess.Process) -> None:
    """SIGKILL the process's whole group, falling back to the process itself."""
    if process.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            process.kill()
        except ProcessLookupError:
            pass


class RuntimeTransport(Protocol):
    """The transport surface the runtime stack (terminal/files/forwards/desktop)
    depends on. Both SSHClient and LocalTransport satisfy it structurally, so the
    same managers work whether the runtime is remote (SSH) or the host itself."""

    async def wait_ready(self, *, timeout: int = 60) -> None: ...
    async def run(
        self,
        command: str,
        *,
        timeout: int = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RuntimeExecResult: ...
    async def run_script(
        self, script: str, *, args: list[str] | None = None, timeout: int = 300
    ) -> RuntimeExecResult: ...
    async def create_process(self, command: str, **kwargs: Any) -> Any: ...
    async def forward_local_port(
        self, listen_host: str, listen_port: int, target_host: str, target_port: int
    ) -> Any: ...
    async def close(self) -> None: ...


class _StreamWriter:
    """Mimics the subset of asyncssh's process.stdin used by callers: a synchronous
    write() (str or bytes) plus write_eof()."""

    def __init__(self, writer: asyncio.StreamWriter, encoding: str | None) -> None:
        self._writer = writer
        self._encoding = encoding

    def write(self, data: str | bytes) -> None:
        if isinstance(data, str):
            data = data.encode(self._encoding or "utf-8", errors="replace")
        self._writer.write(data)

    def write_eof(self) -> None:
        if self._writer.can_write_eof():
            self._writer.write_eof()


class _StreamReader:
    """Mimics process.stdout/stderr: async readline()/read() returning str when an
    encoding is set (as asyncssh does), else raw bytes."""

    def __init__(self, reader: asyncio.StreamReader, encoding: str | None) -> None:
        self._reader = reader
        self._encoding = encoding

    def _decode(self, data: bytes) -> str | bytes:
        return data.decode(self._encoding, errors="replace") if self._encoding else data

    async def readline(self) -> str | bytes:
        return self._decode(await self._reader.readline())

    async def read(self, n: int = -1) -> str | bytes:
        return self._decode(await self._reader.read(n))


class _LocalProcess:
    """Duck-types the asyncssh process returned by create_process (stdin/stdout/
    stderr streams, wait(), exit_status, terminate()) over a local subprocess."""

    def __init__(self, process: asyncio.subprocess.Process, encoding: str | None) -> None:
        self._process = process
        self.stdin = _StreamWriter(process.stdin, encoding) if process.stdin else None
        self.stdout = _StreamReader(process.stdout, encoding) if process.stdout else None
        self.stderr = _StreamReader(process.stderr, encoding) if process.stderr else None

    @property
    def exit_status(self) -> int | None:
        return self._process.returncode

    async def wait(self) -> int:
        return await self._process.wait()

    def terminate(self) -> None:
        _kill_group(self._process)


class _LocalPortForward:
    """Wraps the asyncio relay server with the listener interface callers expect
    (close() + optional wait_closed())."""

    def __init__(self, server: asyncio.AbstractServer) -> None:
        self._server = server

    def close(self) -> None:
        self._server.close()

    async def wait_closed(self) -> None:
        await self._server.wait_closed()


class LocalTransport:
    """Runs runtime commands directly on the host (subprocess + local filesystem)
    instead of over SSH. Used for the `local` runtime in desktop mode, where the
    backend already runs on the user's machine — so there is no machine to SSH to.

    Implements the same surface as SSHClient (see RuntimeTransport) so the terminal/
    files/forwards managers work unchanged. Commands go through the same
    build_shell_command wrapper the SSH path uses, so the seatbelt sandbox and
    workspace scripts behave identically.
    """

    def __init__(self) -> None:
        # The login environment, resolved once on first use and reused for every
        # command (see _capture_login_environment).
        self._env: dict[str, str] | None = None
        self._env_lock = asyncio.Lock()

    async def _environment(self) -> dict[str, str]:
        if self._env is None:
            async with self._env_lock:
                if self._env is None:
                    self._env = await _capture_login_environment()
        return self._env

    async def wait_ready(self, *, timeout: int = 60) -> None:  # noqa: ARG002
        # The host is always reachable — nothing to wait for.
        return None

    async def run(
        self,
        command: str,
        *,
        timeout: int = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RuntimeExecResult:
        inner = build_shell_command(command, cwd=cwd, env=env)
        process = await asyncio.create_subprocess_shell(
            inner,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=await self._environment(),
            start_new_session=True,
        )
        stdout, stderr = await self._communicate(process, timeout=timeout)
        return RuntimeExecResult(
            exit_status=process.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    async def run_script(
        self, script: str, *, args: list[str] | None = None, timeout: int = 300
    ) -> RuntimeExecResult:
        argv = ["bash", "-s", "--", *args] if args else ["bash", "-s"]
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=await self._environment(),
            start_new_session=True,
        )
        stdout, stderr = await self._communicate(
            process, timeout=timeout, stdin=script.encode("utf-8")
        )
        return RuntimeExecResult(
            exit_status=process.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    async def create_process(
        self,
        command: str,
        *,
        term_type: str | None = "xterm-256color",  # noqa: ARG002 — no PTY needed locally
        term_size: tuple[int, int] = (80, 24),  # noqa: ARG002
        encoding: str | None = None,
    ) -> _LocalProcess:
        # Non-login shell with the resolved env (no profile output to corrupt the
        # tmux control stream); stderr merged into stdout, the only consumer read.
        process = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=await self._environment(),
            limit=_TERMINAL_STREAM_LIMIT,
            start_new_session=True,
        )
        return _LocalProcess(process, encoding)

    async def forward_local_port(
        self, listen_host: str, listen_port: int, target_host: str, target_port: int
    ) -> _LocalPortForward:
        # Everything is already on this host, but callers expect listen_port to
        # actually forward — run a small loopback TCP relay so the contract holds.
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                target_reader, target_writer = await asyncio.open_connection(
                    target_host, target_port
                )
            except OSError:
                writer.close()
                return
            try:
                await asyncio.gather(
                    _pump(reader, target_writer),
                    _pump(target_reader, writer),
                    return_exceptions=True,
                )
            finally:
                for stream in (writer, target_writer):
                    try:
                        stream.close()
                    except Exception:  # noqa: BLE001
                        pass

        server = await asyncio.start_server(handle, listen_host, listen_port)
        return _LocalPortForward(server)

    async def close(self) -> None:
        return None

    @staticmethod
    async def _communicate(
        process: asyncio.subprocess.Process, *, timeout: int, stdin: bytes | None = None
    ) -> tuple[bytes, bytes]:
        try:
            return await asyncio.wait_for(process.communicate(input=stdin), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            _kill_group(process)
            await process.wait()
            raise


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except OSError:
        pass
    finally:
        # Half-close the destination so the other direction can still drain its
        # response; handle() fully closes both once both pumps finish.
        try:
            if writer.can_write_eof():
                writer.write_eof()
        except Exception:  # noqa: BLE001
            pass
