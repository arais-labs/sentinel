from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from shlex import quote
from typing import Any, Awaitable, Callable
from uuid import uuid4

from app.services.runtime.environment import RuntimeEnvironment, detect_runtime_environment
from app.services.runtime.remote_commands import load_remote_command
from app.services.runtime.ssh_client import SSHClient
from app.services.runtime.tmux import (
    build_host_tmux_command,
    build_resolve_host_tmux_script,
    build_close_tmux_script,
    build_open_tmux_script,
    build_tmux_status_script,
    tmux_host_log_path,
    tmux_host_socket_path,
    tmux_session_name,
    validate_terminal_id,
)
from app.schemas.runtime import RuntimeExecResult
from app.services.runtime.workspace import (
    build_delete_workspace_script,
    build_prepare_workspace_script,
    workspace_paths,
)

_POLL_INTERVAL_SECONDS = 0.2
_SHELL_FOREGROUND_COMMANDS = {"bash", "sh", "zsh", "fish", "dash"}
_OSC_D_PATTERN = re.compile(rb"\x1b\]133;D(?:;(-?\d+))?(?:\x1b\\|\x07)")


@dataclass(frozen=True, slots=True)
class TerminalStatus:
    session_id: str
    terminal_id: str
    status: str


@dataclass(frozen=True, slots=True)
class TerminalDescriptor:
    terminal_id: str
    label: str
    status: str
    busy: bool
    last_command: str | None = None
    last_cwd: str | None = None
    auto: bool = False
    created_by: str = "runtime"

    def to_dict(self) -> dict[str, object]:
        return {
            "terminal_id": self.terminal_id,
            "label": self.label,
            "status": self.status,
            "busy": self.busy,
            "last_command": self.last_command,
            "last_cwd": self.last_cwd,
            "auto": self.auto,
            "created_by": self.created_by,
        }


@dataclass(frozen=True, slots=True)
class BackgroundJobHandle:
    id: str
    session_id: str
    terminal_id: str
    command: str
    status: str
    run_path: str
    result_path: str
    log_offset: int


class TerminalBlockedError(RuntimeError):
    def __init__(self, reason: str, *, current_command: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.current_command = current_command


class TerminalUnavailableError(RuntimeError):
    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class RuntimeSandboxUnavailableError(TerminalUnavailableError):
    pass


class RuntimeTerminalManager:
    """Backend-owned workspace and tmux lifecycle over SSH."""

    def __init__(self, ssh: SSHClient, *, workspaces_root: str | None = None) -> None:
        self._ssh = ssh
        self._workspaces_root = workspaces_root
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._running_terminals: set[tuple[str, str]] = set()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._background_tasks_by_terminal: dict[tuple[str, str], set[asyncio.Task[None]]] = {}
        self._terminal_metadata: dict[tuple[str, str], dict[str, object]] = {}
        self._environment: RuntimeEnvironment | None = None

    @property
    def ssh(self) -> SSHClient:
        return self._ssh

    @property
    def workspaces_root(self) -> str | None:
        return self._workspaces_root

    async def runtime_environment(self) -> RuntimeEnvironment:
        return await self._require_supported_environment()

    async def prepare_workspace(self, session_id: str) -> None:
        await self._require_supported_environment()
        script, args = build_prepare_workspace_script(session_id, root=self._workspaces_root)
        await self._run_required_script(
            script,
            args,
            timeout=60,
            reason="workspace_prepare_failed",
        )

    async def delete_workspace(self, session_id: str) -> None:
        terminal_ids = {
            terminal_id
            for current_session, terminal_id in self._running_terminals
            if current_session == session_id
        }
        terminal_ids.add("0")
        for terminal_id in terminal_ids:
            await self.close_terminal(session_id, terminal_id=terminal_id)
        script, args = build_delete_workspace_script(session_id, root=self._workspaces_root)
        await self._run_required_script(
            script,
            args,
            timeout=60,
            reason="workspace_delete_failed",
        )

    async def open_terminal(self, session_id: str, *, terminal_id: str = "0") -> TerminalStatus:
        validate_terminal_id(terminal_id)
        lock = self._lock_for(session_id, terminal_id)
        async with lock:
            return await self._open_terminal_unlocked(session_id, terminal_id=terminal_id)

    async def _open_terminal_unlocked(
        self, session_id: str, *, terminal_id: str = "0"
    ) -> TerminalStatus:
        key = (session_id, terminal_id)
        if key in self._running_terminals:
            return TerminalStatus(session_id=session_id, terminal_id=terminal_id, status="running")
        await self.prepare_workspace(session_id)
        environment = await self._require_supported_environment()
        script, args = build_open_tmux_script(
            session_id,
            terminal_id=terminal_id,
            root=self._workspaces_root,
            os_name=environment.os,
            sandbox=environment.sandbox,
        )
        await self._run_required_script(
            script,
            args,
            timeout=30,
            reason="terminal_open_failed",
        )
        self._running_terminals.add(key)
        self._terminal_metadata.setdefault(
            key,
            {
                "label": "main" if terminal_id == "0" else terminal_id,
                "created_by": "runtime",
                "auto": terminal_id.startswith("bg-"),
                "last_command": None,
                "last_cwd": None,
            },
        )
        return TerminalStatus(session_id=session_id, terminal_id=terminal_id, status="running")

    async def close_terminal(self, session_id: str, *, terminal_id: str = "0") -> TerminalStatus:
        validate_terminal_id(terminal_id)
        environment = await self._require_supported_environment()
        script, args = build_close_tmux_script(
            session_id,
            terminal_id=terminal_id,
            root=self._workspaces_root,
            os_name=environment.os,
        )
        result = await self._ssh.run_script(
            script,
            args=args,
            timeout=30,
        )
        if result.exit_status not in {0, None}:
            raise TerminalUnavailableError(
                "terminal_close_failed",
                detail=(result.stderr or result.stdout or "").strip()[:500],
            )
        key = (session_id, terminal_id)
        for task in list(self._background_tasks_by_terminal.get(key, set())):
            task.cancel()
        self._running_terminals.discard(key)
        self._terminal_metadata.pop(key, None)
        return TerminalStatus(session_id=session_id, terminal_id=terminal_id, status="stopped")

    async def status(self, session_id: str, *, terminal_id: str = "0") -> TerminalStatus:
        validate_terminal_id(terminal_id)
        environment = await self._require_supported_environment()
        script, args = build_tmux_status_script(
            session_id,
            terminal_id=terminal_id,
            root=self._workspaces_root,
            os_name=environment.os,
        )
        result = await self._ssh.run_script(
            script,
            args=args,
            timeout=15,
        )
        if result.exit_status not in {0, None}:
            raise TerminalUnavailableError(
                "terminal_status_failed",
                detail=(result.stderr or result.stdout or "").strip()[:500],
            )
        status = (
            (result.stdout or "").strip().splitlines()[-1] if result.stdout.strip() else "unknown"
        )
        key = (session_id, terminal_id)
        if status == "running":
            self._running_terminals.add(key)
        elif status in {"missing", "stopped"}:
            self._running_terminals.discard(key)
        return TerminalStatus(session_id=session_id, terminal_id=terminal_id, status=status)

    async def run_command(
        self,
        session_id: str,
        command: str,
        *,
        terminal_id: str = "0",
        timeout: int = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> RuntimeExecResult:
        validate_terminal_id(terminal_id)
        timeout = max(1, int(timeout or 300))
        lock = self._lock_for(session_id, terminal_id)
        async with lock:
            await self._open_terminal_unlocked(session_id, terminal_id=terminal_id)
            await self._refuse_if_foreground_busy(session_id, terminal_id=terminal_id)
            offset = await self._pane_log_size(session_id, terminal_id=terminal_id)
            await self._send_to_pane(
                session_id,
                terminal_id=terminal_id,
                command=self._build_visible_command(command, cwd=cwd, env=env),
            )
            self._remember_terminal_command(
                session_id,
                terminal_id,
                command=command,
                cwd=cwd,
                created_by="agent",
                auto=terminal_id.startswith("bg-"),
            )
            return await self._await_command_complete(
                session_id,
                terminal_id=terminal_id,
                since_offset=offset,
                timeout=timeout,
            )

    async def start_background_command(
        self,
        session_id: str,
        command: str,
        *,
        terminal_id: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        on_complete: Callable[[dict[str, object], str, str], Awaitable[None]] | None = None,
        on_terminal_idle: Callable[[], Awaitable[None]] | None = None,
        defer_watch: bool = False,
    ) -> BackgroundJobHandle:
        job_id = uuid4().hex
        terminal_id = validate_terminal_id(terminal_id or f"bg-{job_id[:8]}")
        if terminal_id == "0":
            raise TerminalUnavailableError(
                "background_terminal_invalid",
                detail="Background runtime commands cannot use terminal 0.",
            )
        lock = self._lock_for(session_id, terminal_id)
        async with lock:
            await self._open_terminal_unlocked(session_id, terminal_id=terminal_id)
            await self._refuse_if_foreground_busy(session_id, terminal_id=terminal_id)
            offset = await self._pane_log_size(session_id, terminal_id=terminal_id)
            handle = await self._write_background_job_script(
                session_id,
                job_id=job_id,
                terminal_id=terminal_id,
                command=command,
                cwd=cwd,
                env=env,
                log_offset=offset,
            )
            await self._send_to_pane(
                session_id,
                terminal_id=terminal_id,
                command=f"bash {quote(handle.run_path)}",
            )
            self._remember_terminal_command(
                session_id,
                terminal_id,
                command=command,
                cwd=cwd,
                created_by="agent",
                auto=terminal_id.startswith("bg-"),
            )
        if not defer_watch:
            self.watch_background_command(
                handle,
                on_complete=on_complete,
                on_terminal_idle=on_terminal_idle,
            )
        return handle

    def watch_background_command(
        self,
        handle: BackgroundJobHandle,
        *,
        on_complete: Callable[[dict[str, object], str, str], Awaitable[None]] | None = None,
        on_terminal_idle: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._watch_background_command(
                handle,
                on_complete=on_complete,
                on_terminal_idle=on_terminal_idle,
            )
        )
        self._track_background_task((handle.session_id, handle.terminal_id), task)

    async def list_terminals(
        self,
        session_id: str,
        *,
        terminal_ids: list[str] | None = None,
    ) -> list[TerminalDescriptor]:
        requested = {validate_terminal_id(item) for item in terminal_ids} if terminal_ids else None
        discovered = await self._discover_running_terminal_ids(session_id)
        for terminal_id in discovered:
            self._running_terminals.add((session_id, terminal_id))
        for current_session, terminal_id in list(self._running_terminals):
            if current_session != session_id:
                continue
            if terminal_id not in discovered:
                self._running_terminals.discard((current_session, terminal_id))
        terminal_ids_to_report = sorted(
            discovered | {item[1] for item in self._running_terminals if item[0] == session_id}
        )
        if requested is not None:
            terminal_ids_to_report = [
                terminal_id for terminal_id in terminal_ids_to_report if terminal_id in requested
            ]
        return [
            self._descriptor_for(session_id, terminal_id) for terminal_id in terminal_ids_to_report
        ]

    async def read_tails(
        self,
        session_id: str,
        *,
        terminal_ids: list[str],
        tail_bytes: int = 8_000,
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for terminal_id in terminal_ids:
            terminal_id = validate_terminal_id(terminal_id)
            try:
                output = await self.read_tail(
                    session_id, terminal_id=terminal_id, tail_bytes=tail_bytes
                )
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {"terminal_id": terminal_id, "ok": False, "error": str(exc), "output": ""}
                )
                continue
            results.append({"terminal_id": terminal_id, "ok": True, "output": output})
        return results

    async def close_terminals(
        self,
        session_id: str,
        *,
        terminal_ids: list[str],
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for terminal_id in terminal_ids:
            terminal_id = validate_terminal_id(terminal_id)
            existed = (session_id, terminal_id) in self._running_terminals
            try:
                status = await self.close_terminal(session_id, terminal_id=terminal_id)
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {"terminal_id": terminal_id, "closed": False, "ok": False, "error": str(exc)}
                )
                continue
            results.append(
                {
                    "terminal_id": terminal_id,
                    "closed": True,
                    "existed": existed,
                    "ok": True,
                    "status": status.status,
                }
            )
        return results

    async def read_tail(
        self,
        session_id: str,
        *,
        terminal_id: str = "0",
        tail_bytes: int = 8_000,
    ) -> str:
        count = max(256, min(int(tail_bytes), 200_000))
        socket = tmux_host_socket_path(
            session_id, terminal_id=terminal_id, root=self._workspaces_root
        )
        name = tmux_session_name(terminal_id)
        history_lines = max(100, min(5_000, (count // 80) + 100))
        command = await self._tmux_command(
            [
                "-S",
                socket,
                "capture-pane",
                "-p",
                "-J",
                "-t",
                name,
                "-S",
                f"-{history_lines}",
            ]
        )
        result = await self._ssh.run(f"{command} 2>/dev/null", timeout=15)
        if result.exit_status not in {0, None}:
            raise TerminalUnavailableError(
                "terminal_read_failed",
                detail=(result.stderr or result.stdout or "").strip()[:500],
            )
        return _truncate_terminal_text((result.stdout or ""), max_chars=count)

    async def attach_ws(self, session_id: str, terminal_id: str, websocket: Any) -> None:
        validate_terminal_id(terminal_id)
        if (session_id, terminal_id) not in self._running_terminals:
            await self.open_terminal(session_id, terminal_id=terminal_id)
        socket = tmux_host_socket_path(
            session_id, terminal_id=terminal_id, root=self._workspaces_root
        )
        name = tmux_session_name(terminal_id)
        command = await self._tmux_command(["-S", socket, "-C", "attach-session", "-t", name])
        process = await self._ssh.create_process(
            command,
            term_type=None,
            encoding="utf-8",
        )
        out_task: asyncio.Task[None] | None = None
        try:
            process.stdin.write(f"refresh-client -C 80x24\n")
            process.stdin.write(f"capture-pane -p -e -t {name}\n")

            async def out_pump() -> None:
                in_command_block = False
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        return
                    if line.startswith("%begin "):
                        in_command_block = True
                        continue
                    if line.startswith("%end ") or line.startswith("%error "):
                        in_command_block = False
                        continue
                    data = (
                        line.encode("utf-8", errors="replace")
                        if in_command_block
                        else _parse_tmux_control_output(line)
                    )
                    if data:
                        await websocket.send_bytes(data)

            out_task = asyncio.create_task(out_pump())
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                if message.get("type") != "websocket.receive":
                    continue
                text = message.get("text")
                if isinstance(text, str) and text.startswith("{"):
                    try:
                        import json

                        payload = json.loads(text)
                    except Exception:
                        payload = None
                    if isinstance(payload, dict) and payload.get("type") == "resize":
                        cols = max(20, int(payload.get("cols") or 80))
                        rows = max(5, int(payload.get("rows") or 24))
                        process.stdin.write(f"refresh-client -C {cols}x{rows}\n")
                        continue
                raw = message.get("bytes")
                data = (
                    raw
                    if raw is not None
                    else text.encode("utf-8", errors="replace") if text is not None else b""
                )
                if data:
                    _write_tmux_control_keys(process, name, data)
        finally:
            if out_task is not None:
                out_task.cancel()
                try:
                    await out_task
                except (asyncio.CancelledError, Exception):
                    pass
            process.terminate()

    async def close(self) -> None:
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
        await self._ssh.close()

    def _track_background_task(self, key: tuple[str, str], task: asyncio.Task[None]) -> None:
        self._background_tasks.add(task)
        self._background_tasks_by_terminal.setdefault(key, set()).add(task)

        def _discard(done: asyncio.Task[None]) -> None:
            self._background_tasks.discard(done)
            terminal_tasks = self._background_tasks_by_terminal.get(key)
            if terminal_tasks is None:
                return
            terminal_tasks.discard(done)
            if not terminal_tasks:
                self._background_tasks_by_terminal.pop(key, None)

        task.add_done_callback(_discard)

    def _remember_terminal_command(
        self,
        session_id: str,
        terminal_id: str,
        *,
        command: str,
        cwd: str | None,
        created_by: str,
        auto: bool,
    ) -> None:
        key = (session_id, terminal_id)
        metadata = self._terminal_metadata.setdefault(
            key,
            {
                "label": "main" if terminal_id == "0" else terminal_id,
                "created_by": created_by,
                "auto": auto,
            },
        )
        metadata["last_command"] = command
        metadata["last_cwd"] = cwd
        metadata["created_by"] = created_by
        metadata["auto"] = auto

    def _descriptor_for(self, session_id: str, terminal_id: str) -> TerminalDescriptor:
        key = (session_id, terminal_id)
        metadata = self._terminal_metadata.get(key, {})
        busy = any(not task.done() for task in self._background_tasks_by_terminal.get(key, set()))
        return TerminalDescriptor(
            terminal_id=terminal_id,
            label=str(metadata.get("label") or ("main" if terminal_id == "0" else terminal_id)),
            status="running",
            busy=busy,
            last_command=(
                str(metadata["last_command"]) if metadata.get("last_command") is not None else None
            ),
            last_cwd=str(metadata["last_cwd"]) if metadata.get("last_cwd") is not None else None,
            auto=bool(
                metadata.get("auto") if "auto" in metadata else terminal_id.startswith("bg-")
            ),
            created_by=str(metadata.get("created_by") or "runtime"),
        )

    async def _discover_running_terminal_ids(self, session_id: str) -> set[str]:
        paths = workspace_paths(session_id, root=self._workspaces_root)
        environment = await self._require_supported_environment()
        script = (
            load_remote_command("common/tmux/discover.sh")
            .replace("__TMUX_DIR__", quote(paths.tmux))
            .replace(
                "__RESOLVE_HOST_TMUX__", build_resolve_host_tmux_script(os_name=environment.os)
            )
        )
        result = await self._ssh.run_script(script, timeout=15)
        if result.exit_status not in {0, None}:
            raise TerminalUnavailableError(
                "terminal_list_failed",
                detail=(result.stderr or result.stdout or "").strip()[:500],
            )
        return {
            validate_terminal_id(line.strip())
            for line in (result.stdout or "").splitlines()
            if line.strip()
        }

    def _lock_for(self, session_id: str, terminal_id: str) -> asyncio.Lock:
        key = (session_id, terminal_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _require_supported_environment(self) -> RuntimeEnvironment:
        environment = self._environment
        if environment is None:
            environment = await detect_runtime_environment(self._ssh)
            self._environment = environment
        if not environment.supported:
            raise RuntimeSandboxUnavailableError(
                "runtime_sandbox_unavailable",
                detail=(
                    "Runtime must be Linux with bubblewrap or macOS with sandbox-exec "
                    f"(detected os={environment.os}, sandbox={environment.sandbox})."
                ),
            )
        return environment

    async def _run_required_script(
        self, script: str, args: list[str], *, timeout: int, reason: str
    ) -> None:
        result = await self._ssh.run_script(script, args=args, timeout=timeout)
        if result.exit_status not in {0, None}:
            raise TerminalUnavailableError(
                reason,
                detail=(result.stderr or result.stdout or "").strip()[:500],
            )

    async def _refuse_if_foreground_busy(self, session_id: str, *, terminal_id: str) -> None:
        socket = tmux_host_socket_path(
            session_id, terminal_id=terminal_id, root=self._workspaces_root
        )
        name = tmux_session_name(terminal_id)
        command = await self._tmux_command(
            [
                "-S",
                socket,
                "display-message",
                "-p",
                "-t",
                name,
                "#{pane_current_command}",
            ]
        )
        result = await self._ssh.run(
            command,
            timeout=10,
        )
        current = (result.stdout or "").strip()
        if current and current not in _SHELL_FOREGROUND_COMMANDS:
            raise TerminalBlockedError("foreground_process_running", current_command=current)

    async def _pane_log_size(self, session_id: str, *, terminal_id: str) -> int:
        log_path = tmux_host_log_path(
            session_id, terminal_id=terminal_id, root=self._workspaces_root
        )
        environment = await self._require_supported_environment()
        stat_command = (
            f"stat -f %z {quote(log_path)} 2>/dev/null || echo 0"
            if environment.os == "darwin"
            else f"stat -c %s {quote(log_path)} 2>/dev/null || echo 0"
        )
        result = await self._ssh.run(
            stat_command,
            timeout=10,
        )
        try:
            return int((result.stdout or "").strip())
        except (TypeError, ValueError):
            return 0

    async def _send_to_pane(self, session_id: str, *, terminal_id: str, command: str) -> None:
        socket = tmux_host_socket_path(
            session_id, terminal_id=terminal_id, root=self._workspaces_root
        )
        name = tmux_session_name(terminal_id)
        await self._ssh.run(
            await self._tmux_command(["-S", socket, "send-keys", "-t", name, "C-u"]),
            timeout=10,
        )
        if "\n" in command:
            await self._ssh.run(
                await self._tmux_command(
                    ["-S", socket, "send-keys", "-t", name, "-l", "\x1b[200~"]
                ),
                timeout=10,
            )
            await self._ssh.run(
                await self._tmux_command(["-S", socket, "send-keys", "-t", name, "-l", command]),
                timeout=15,
            )
            await self._ssh.run(
                await self._tmux_command(
                    ["-S", socket, "send-keys", "-t", name, "-l", "\x1b[201~"]
                ),
                timeout=10,
            )
        else:
            await self._ssh.run(
                await self._tmux_command(["-S", socket, "send-keys", "-t", name, "-l", command]),
                timeout=15,
            )
        await self._ssh.run(
            await self._tmux_command(["-S", socket, "send-keys", "-t", name, "Enter"]),
            timeout=10,
        )

    async def _tmux_command(self, args: list[str]) -> str:
        environment = await self._require_supported_environment()
        return build_host_tmux_command(args, os_name=environment.os)

    async def _await_command_complete(
        self,
        session_id: str,
        *,
        terminal_id: str,
        since_offset: int,
        timeout: int,
    ) -> RuntimeExecResult:
        log_path = tmux_host_log_path(
            session_id, terminal_id=terminal_id, root=self._workspaces_root
        )
        deadline = asyncio.get_running_loop().time() + timeout
        last_chunk = b""
        while True:
            result = await self._ssh.run(
                f"tail -c +{since_offset + 1} {quote(log_path)} 2>/dev/null || true",
                timeout=10,
            )
            last_chunk = (result.stdout or "").encode("utf-8", errors="replace")
            match = _OSC_D_PATTERN.search(last_chunk)
            if match:
                raw_code = (
                    match.group(1).decode("ascii", errors="replace") if match.group(1) else ""
                )
                exit_code = int(raw_code) if raw_code and raw_code.lstrip("-").isdigit() else -1
                return self._parse_output(last_chunk[: match.start()], exit_code)
            if asyncio.get_running_loop().time() >= deadline:
                parsed = self._parse_output(last_chunk, -1)
                return RuntimeExecResult(
                    exit_status=-1,
                    stdout=parsed.stdout,
                    stderr="[command did not finish within timeout]",
                )
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    def _build_visible_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        if not cwd and not env:
            return command
        parts: list[str] = []
        if cwd:
            parts.append(f"cd {quote(cwd)}")
        if env:
            for key, value in env.items():
                if not key:
                    continue
                parts.append(f"export {key}={quote(str(value))}")
        if "\n" in command:
            setup = list(parts)
            if cwd:
                setup[0] = f"cd {quote(cwd)} || exit"
            body = command.rstrip("\n")
            return "(\n" + "\n".join([*setup, body, ")"])
        if cwd:
            prefix = parts[0] + " && " + "; ".join(parts[1:] + [command])
        else:
            prefix = "; ".join(parts + [command])
        return f"({prefix})"

    async def _write_background_job_script(
        self,
        session_id: str,
        *,
        job_id: str,
        terminal_id: str,
        command: str,
        cwd: str | None,
        env: dict[str, str] | None,
        log_offset: int,
    ) -> BackgroundJobHandle:
        paths = workspace_paths(session_id, root=self._workspaces_root)
        host_job_dir = (PurePosixPath(paths.runtime) / "jobs" / job_id).as_posix()
        inner_job_dir = (PurePosixPath("/state/runtime/jobs") / job_id).as_posix()
        inner_run_path = (PurePosixPath(inner_job_dir) / "run.sh").as_posix()
        inner_done_path = (PurePosixPath(inner_job_dir) / "done.json").as_posix()
        host_done_path = (PurePosixPath(host_job_dir) / "done.json").as_posix()
        manifest = {
            "id": job_id,
            "session_id": session_id,
            "terminal_id": terminal_id,
            "command": command,
            "cwd": cwd,
            "status": "running",
            "created_at": _utc_now(),
            "started_at": _utc_now(),
        }
        request = {
            "job_dir": host_job_dir,
            "runner": load_remote_command("common/jobs/run.sh"),
            "config": {
                "job_id": job_id,
                "command": command,
                "cwd": cwd,
                "env": env or {},
                "done_path": inner_done_path,
            },
            "manifest": manifest,
        }
        result = await self._ssh.run_script(
            load_remote_command("common/jobs/write.sh"),
            args=[json.dumps(request, separators=(",", ":"))],
            timeout=30,
        )
        if result.exit_status not in {0, None}:
            raise TerminalUnavailableError(
                "background_job_prepare_failed",
                detail=(result.stderr or result.stdout or "").strip()[:500],
            )
        return BackgroundJobHandle(
            id=job_id,
            session_id=session_id,
            terminal_id=terminal_id,
            command=command,
            status="running",
            run_path=inner_run_path,
            result_path=host_done_path,
            log_offset=log_offset,
        )

    async def _watch_background_command(
        self,
        handle: BackgroundJobHandle,
        *,
        on_complete: Callable[[dict[str, object], str, str], Awaitable[None]] | None,
        on_terminal_idle: Callable[[], Awaitable[None]] | None,
    ) -> None:
        stdout_tail = ""
        cancelled = False
        job: dict[str, object] = {
            "id": handle.id,
            "status": "failed",
            "command": handle.command,
            "terminal_id": handle.terminal_id,
            "returncode": None,
        }
        try:
            result = await self._await_command_complete(
                handle.session_id,
                terminal_id=handle.terminal_id,
                since_offset=handle.log_offset,
                timeout=7 * 24 * 60 * 60,
            )
            stdout_tail = result.stdout
            done = await self._read_background_job_result(handle)
            if done is not None:
                job.update(done)
            else:
                job.update(
                    {
                        "status": "completed" if result.exit_status == 0 else "failed",
                        "returncode": result.exit_status,
                        "ended_at": _utc_now(),
                    }
                )
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:  # noqa: BLE001
            job.update({"status": "failed", "error": str(exc), "ended_at": _utc_now()})
        finally:
            if on_terminal_idle is not None:
                try:
                    await on_terminal_idle()
                except Exception:  # noqa: BLE001
                    pass
            if not cancelled and on_complete is not None:
                await on_complete(job, stdout_tail[-8_000:], "")

    async def _read_background_job_result(
        self, handle: BackgroundJobHandle
    ) -> dict[str, object] | None:
        result = await self._ssh.run(
            f"test -f {quote(handle.result_path)} && cat {quote(handle.result_path)} || true",
            timeout=15,
        )
        text = (result.stdout or "").strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _parse_output(self, raw: bytes, exit_code: int) -> RuntimeExecResult:
        text = _clean_terminal_text(raw)
        newline = text.find("\n")
        output = text[newline + 1 :] if newline >= 0 else ""
        return RuntimeExecResult(exit_status=exit_code, stdout=output.rstrip(), stderr="")


def get_terminal_manager(
    ssh: SSHClient, *, workspaces_root: str | None = None
) -> RuntimeTerminalManager:
    return RuntimeTerminalManager(ssh, workspaces_root=workspaces_root)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_terminal_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    lines: list[str] = []
    line: list[str] = []
    cursor = 0
    state = "normal"

    def put(char: str) -> None:
        nonlocal cursor
        if cursor >= len(line):
            line.extend(" " for _ in range(cursor - len(line)))
            line.append(char)
        else:
            line[cursor] = char
        cursor += 1

    def newline() -> None:
        nonlocal cursor, line
        lines.append("".join(line).rstrip())
        line = []
        cursor = 0

    for char in text:
        code = ord(char)
        if state == "esc":
            if char == "[":
                state = "csi"
            elif char == "]":
                state = "osc"
            elif char in {"P", "^", "_"}:
                state = "st"
            elif char in {"(", ")", "*", "+", "-", ".", "/", "#", "%"}:
                state = "esc_one"
            else:
                state = "normal"
            continue
        if state == "esc_one":
            state = "normal"
            continue
        if state == "csi":
            if 0x40 <= code <= 0x7E:
                state = "normal"
            continue
        if state == "osc":
            if char == "\x07":
                state = "normal"
            elif char == "\x1b":
                state = "osc_esc"
            continue
        if state == "osc_esc":
            state = "normal" if char == "\\" else "osc"
            continue
        if state == "st":
            if char == "\x1b":
                state = "st_esc"
            continue
        if state == "st_esc":
            state = "normal" if char == "\\" else "st"
            continue

        if char == "\x1b":
            state = "esc"
        elif char == "\n":
            newline()
        elif char == "\r":
            cursor = 0
        elif char == "\b":
            cursor = max(0, cursor - 1)
        elif char == "\t":
            put(char)
        elif code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
            continue
        else:
            put(char)

    lines.append("".join(line).rstrip())
    return "\n".join(lines)


def _truncate_terminal_text(text: str, *, max_chars: int) -> str:
    text = text.replace("\x00", "")
    if len(text) <= max_chars:
        return text.rstrip()
    clipped = text[-max_chars:]
    newline = clipped.find("\n")
    if newline >= 0:
        clipped = clipped[newline + 1 :]
    return clipped.rstrip()


def _parse_tmux_control_output(line: str) -> bytes:
    if line.startswith("%output "):
        parts = line.rstrip("\n").split(" ", 2)
        if len(parts) == 3:
            return _decode_tmux_control_value(parts[2])
    if line.startswith("%extended-output "):
        prefix, _, value = line.rstrip("\n").partition(" : ")
        if prefix and value:
            return _decode_tmux_control_value(value)
    if line.startswith("%"):
        return b""
    return b""


def _decode_tmux_control_value(value: str) -> bytes:
    output = bytearray()
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 3 < len(value) and value[index + 1 : index + 4].isdigit():
            try:
                output.append(int(value[index + 1 : index + 4], 8))
                index += 4
                continue
            except ValueError:
                pass
        output.extend(char.encode("utf-8", errors="replace"))
        index += 1
    return bytes(output)


def _write_tmux_control_keys(process: Any, target: str, data: bytes) -> None:
    for offset in range(0, len(data), 128):
        chunk = data[offset : offset + 128]
        if not chunk:
            continue
        hex_bytes = " ".join(f"{byte:02x}" for byte in chunk)
        process.stdin.write(f"send-keys -t {target} -H {hex_bytes}\n")
