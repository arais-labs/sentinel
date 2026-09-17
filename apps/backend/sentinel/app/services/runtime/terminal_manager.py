from __future__ import annotations

import asyncio
import logging
import re
from shlex import quote
from typing import Any

from app.schemas.runtime import RuntimeExecResult
import app.services.runtime.container_transport as container_transport
from app.services.runtime.environment import RuntimeEnvironment, detect_runtime_environment
from app.services.runtime.local_transport import RuntimeTransport
from app.services.runtime.terminal_view import serve_terminal
from app.services.runtime.tmux import (
    build_host_tmux_command,
    build_open_tmux_script,
    tmux_host_socket_path,
)
from app.services.runtime.workspace import (
    WorkspaceLocation,
    build_delete_session_script,
    build_prepare_workspace_script,
)

_POLL_INTERVAL_SECONDS = 0.2
logger = logging.getLogger(__name__)
_OSC_D_PATTERN = re.compile(rb"\x1b\]133;D(?:;(-?\d+))?(?:\x1b\\|\x07)")
_OSC_C_PATTERN = re.compile(rb"\x1b\]133;C(?:\x1b\\|\x07)")


class TerminalUnavailableError(RuntimeError):
    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class RuntimeSandboxUnavailableError(TerminalUnavailableError):
    pass


class RuntimeTerminalManager:
    """Tmux session lifecycle and viewer bridge over a machine transport."""

    def __init__(self, ssh: RuntimeTransport, *, workspace_location: WorkspaceLocation) -> None:
        self._ssh = ssh
        self._workspace_location = workspace_location
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._background_tasks_by_pane: dict[tuple[str, str], set[asyncio.Task[Any]]] = {}
        self._environment: RuntimeEnvironment | None = None
        self._open_locks: dict[str, asyncio.Lock] = {}

    @property
    def ssh(self) -> RuntimeTransport:
        return self._ssh

    @property
    def workspace_location(self) -> WorkspaceLocation:
        return self._workspace_location

    async def runtime_environment(self) -> RuntimeEnvironment:
        return await self._require_supported_environment()

    async def prepare_workspace(self, session_id: str) -> None:
        await self._require_supported_environment()
        script, args = build_prepare_workspace_script(session_id, root=self._workspace_location)
        await self._run_required_script(
            script,
            args,
            timeout=60,
            reason="workspace_prepare_failed",
        )

    async def delete_session_state(self, session_id: str) -> None:
        socket = tmux_host_socket_path(session_id, root=self._workspace_location)
        await self._ssh.run(
            (await self._tmux_command(["-S", socket, "kill-server"])) + " 2>/dev/null || true",
            timeout=15,
        )
        for (chat, pane), tasks in list(self._background_tasks_by_pane.items()):
            if chat == session_id:
                for task in list(tasks):
                    task.cancel()
        script, args = build_delete_session_script(session_id, root=self._workspace_location)
        await self._run_required_script(script, args, timeout=60, reason="session_delete_failed")

    async def ensure_session(self, session_id: str) -> None:
        async with self._open_locks.setdefault(session_id, asyncio.Lock()):
            await self.prepare_workspace(session_id)
            environment = await self._require_supported_environment()
            script, args = build_open_tmux_script(
                session_id,
                root=self._workspace_location,
                os_name=environment.os,
                sandbox=environment.sandbox,
            )
            await self._run_required_script(
                script, args, timeout=30, reason="tmux_session_open_failed"
            )

    async def attach_ws(
        self, session_id: str, websocket: Any, *, on_panes=None, retry_setup=False
    ) -> None:
        # Observe disconnects while setup is pending, before a guest process exists.
        # A closed UI must not keep an HTTP worker alive through a download.
        messages: asyncio.Queue = asyncio.Queue(maxsize=16)

        async def receive() -> None:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                if len(message.get("bytes") or message.get("text") or "") > 1024 * 1024:
                    await websocket.close(code=1009)
                    return
                try:
                    messages.put_nowait(message)
                except asyncio.QueueFull:
                    await websocket.close(code=1009)
                    return

        tasks = [
            asyncio.create_task(receive()),
            asyncio.create_task(
                self._attach_ws(
                    session_id, websocket, messages, on_panes=on_panes, retry_setup=retry_setup
                )
            ),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _attach_ws(
        self,
        session_id: str,
        websocket: Any,
        messages: asyncio.Queue,
        *,
        on_panes=None,
        retry_setup=False,
    ) -> None:
        if retry_setup:

            if isinstance(self._ssh, container_transport.ContainerTransport):
                await self._ssh.wait_ready(retry=True)
        await self.ensure_session(session_id)

        await serve_terminal(self, session_id, websocket, messages, on_panes=on_panes)

    async def close(self) -> None:
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*list(self._background_tasks), return_exceptions=True)
        await self._ssh.close()

    def _track_background_task(self, key: tuple[str, str], task: asyncio.Task[Any]) -> None:
        self._background_tasks.add(task)
        self._background_tasks_by_pane.setdefault(key, set()).add(task)

        def _discard(done: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(done)
            if not done.cancelled() and (error := done.exception()) is not None:
                logger.error("Runtime background task failed for %s", key, exc_info=error)
            pane_tasks = self._background_tasks_by_pane.get(key)
            if pane_tasks is None:
                return
            pane_tasks.discard(done)
            if not pane_tasks:
                self._background_tasks_by_pane.pop(key, None)

        task.add_done_callback(_discard)

    def _lock_for(self, session_id: str, pane_id: str) -> asyncio.Lock:
        key = (session_id, pane_id)
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
                    "An attached workspace container is required "
                    f"(detected os={environment.os}, sandbox={environment.sandbox})."
                ),
            )
        return environment

    async def _run_required_script(
        self, script: str, args: list[str], *, timeout: int, reason: str
    ) -> RuntimeExecResult:
        result = await self._ssh.run_script(script, args=args, timeout=timeout)
        if result.exit_status not in {0, None}:
            raise TerminalUnavailableError(
                reason,
                detail=(result.stderr or result.stdout or "").strip()[:500],
            )
        return result

    async def _tmux_command(self, args: list[str]) -> str:
        environment = await self._require_supported_environment()
        return build_host_tmux_command(args, os_name=environment.os)

    async def _await_command_complete(
        self,
        session_id: str,
        *,
        since_offset: int,
        log_path: str,
        pane_id: str,
    ) -> RuntimeExecResult:
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
                pre_d = last_chunk[: match.start()]
                # Output starts at the OSC 133;C marker; fall back to dropping the
                # echoed first line if shell integration did not emit a start marker.
                c_matches = list(_OSC_C_PATTERN.finditer(pre_d))
                if c_matches:
                    return self._parse_output(
                        pre_d[c_matches[-1].end() :], exit_code, strip_command_echo=False
                    )
                return self._parse_output(pre_d, exit_code)
            if pane_id:
                socket = tmux_host_socket_path(session_id, root=self._workspace_location)
                state = await self._ssh.run(
                    await self._tmux_command(
                        [
                            "-S",
                            socket,
                            "display-message",
                            "-p",
                            "-t",
                            pane_id,
                            "#{pane_dead}|#{pane_dead_status}",
                        ]
                    ),
                    timeout=10,
                )
                if state.exit_status != 0 or state.stdout.startswith("1|"):
                    parsed = self._parse_output(last_chunk, -1)
                    return RuntimeExecResult(
                        exit_status=-1,
                        stdout=parsed.stdout,
                        stderr="Pane exited before reporting command completion",
                    )
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    def _build_visible_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        parts: list[str] = []
        if cwd:
            parts.append(f"cd {quote(cwd)}")
        if env:
            for key, value in env.items():
                if not key:
                    continue
                parts.append(f"export {key}={quote(str(value))}")
        if "\n" in command:
            # Wrap multiline in a subshell so it runs as one command (one marker)
            # instead of submitting each line — which would split a heredoc.
            setup = list(parts)
            if cwd:
                setup[0] = f"cd {quote(cwd)} || exit"
            body = command.rstrip("\n")
            script = "(\n" + "\n".join([*setup, body, ")"])
            # One physical input line avoids Readline repeatedly echoing queued
            # multiline input at continuation prompts (including on Bash 3.2).
            # ANSI-C quoting preserves the script without expanding its contents
            # until eval executes it. Keep the subshell's existing isolation.
            escaped = "".join(
                (
                    "\\n"
                    if char == "\n"
                    else (
                        f"\\{ord(char):03o}"
                        if ord(char) < 32 or ord(char) == 127
                        else "\\" + char if char in "\\'" else char
                    )
                )
                for char in script
            )
            return "eval $'" + escaped + "'"
        if not parts:
            return command
        if cwd:
            prefix = parts[0] + " && " + "; ".join(parts[1:] + [command])
        else:
            prefix = "; ".join(parts + [command])
        return f"({prefix})"

    def _parse_output(
        self, raw: bytes, exit_code: int, *, strip_command_echo: bool = True
    ) -> RuntimeExecResult:
        text = _clean_terminal_text(raw)
        if strip_command_echo:
            # No C marker: the first line is the echoed command — drop it.
            newline = text.find("\n")
            text = text[newline + 1 :] if newline >= 0 else ""
        return RuntimeExecResult(exit_status=exit_code, stdout=text.rstrip(), stderr="")


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
