"""Persistent tmux-backed terminals routed through SSH-backed runtimes.

Every agent runtime.user command lands inside a named tmux session in the
guest VM. The same session is exposed to the human user over a WebSocket so
they can attach via xterm.js, scroll back, type, and send signals — true
multi-attach co-piloting.

Agent output is captured to per-command files inside the workspace so that
user keystrokes in the pane cannot corrupt the bytes the agent reads back.
The tmux pane visually reflects everything (agent commands AND user input)
but the agent's exit-code/stdout/stderr come from the files only.

Root commands continue to bypass tmux (handled directly by SSHClient with
sudo) since they are gated by approval and traditionally run one-shot. This
keeps the v1 surface small.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from fastapi import WebSocketDisconnect

from app.services.runtime import get_runtime
from app.services.runtime.base import RuntimeExecResult, RuntimeTerminalSession


# Minimal rcfile written into every terminal's workspace and sourced by the
# pane's `bash --rcfile <path> -i`. The session user was created with
# `useradd -M` so /etc/skel/.bashrc never landed in their home — without
# something equivalent the shell starts with no color aliases and a bare
# `\$ ` prompt. This rc:
#   - inherits the system default if present (so per-distro tweaks still
#     apply)
#   - turns on color for the usual suspects (ls, grep, diff, ip, etc.)
#   - gives a readable PS1 with cwd
#   - sets LESS=-R so paged output keeps its ANSI colors
_SENTINEL_BASHRC = r"""# Sentinel terminal init — colors, readable prompt, and OSC 133 prompt-boundary
# markers so backend tooling can find command boundaries in the pipe-pane log
# without leaving any visible noise in the pane.
if [ -r /etc/bash.bashrc ]; then
  . /etc/bash.bashrc
fi
export TERM="${TERM:-xterm-256color}"
export COLORTERM="${COLORTERM:-truecolor}"
export CLICOLOR=1
export LESS='-R'
alias ls='ls --color=auto'
alias ll='ls -lah --color=auto'
alias la='ls -A --color=auto'
alias l='ls -CF --color=auto'
alias grep='grep --color=auto'
alias egrep='egrep --color=auto'
alias fgrep='fgrep --color=auto'
alias diff='diff --color=auto'
alias ip='ip --color=auto'
if command -v dircolors >/dev/null 2>&1; then
  eval "$(dircolors -b 2>/dev/null || true)"
fi
# OSC 133 ("FinalTerm") prompt boundary markers. These render as ZERO visible
# characters in any modern terminal — they're a documented escape sequence
# that decent terminals (xterm.js included) silently consume. The agent reads
# the pipe-pane log and uses these markers to find "this is where my command
# ended, exit code was X" without any visible plumbing in the pane.
#
#   A: prompt start. B: prompt end / command input start. D;<exit>: command
#   output end (emitted by PROMPT_COMMAND right before the next prompt).
__sentinel_osc_d() {
  local __rc=$?
  printf '\033]133;D;%s\033\\' "$__rc"
  return $__rc
}
PROMPT_COMMAND='__sentinel_osc_d'
PS1='\[\033]133;A\033\\\]\[\e[1;36m\]\u\[\e[0m\]@\[\e[1;32m\]\h\[\e[0m\]:\[\e[1;34m\]\w\[\e[0m\]\$ \[\033]133;B\033\\\]'
"""

if TYPE_CHECKING:
    from fastapi import WebSocket

    from app.services.runtime.base import RuntimeInstance
    from app.services.runtime.ssh_client import SSHClient


logger = logging.getLogger(__name__)

# Bounds on how long we wait for a single agent command's completion marker
# to appear in the pipe-pane log. The handler always passes an explicit
# timeout, but fall back to a sane default if we get None.
_DEFAULT_RUN_TIMEOUT_SECONDS = 300
_POLL_INTERVAL_SECONDS = 0.2
_DEFAULT_TERMINAL_ID = "0"
_TERMINAL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
_DEFAULT_TMUX_COLS = 200
_DEFAULT_TMUX_ROWS = 50
_TMUX_HISTORY_LIMIT = 50_000
# OSC 133 D marker — emitted by our bashrc's PROMPT_COMMAND right before each
# new prompt is drawn. Carries the previous command's exit code as a numeric
# parameter. ST can be either ESC-backslash or BEL.
_OSC_D_PATTERN = re.compile(rb"\x1b\]133;D(?:;(-?\d+))?(?:\x1b\\|\x07)")
# Generic ANSI/CSI/OSC escape stripper for cleaning output before returning to
# the model. Matches: CSI ("\x1b[..."), OSC ("\x1b]...ST"), and standalone ESC.
_ANSI_PATTERN = re.compile(
    rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[a-zA-Z]|\x1b[\x20-\x2f]*[\x30-\x7e]"
)


# Optional broadcaster injected at app startup. Signature mirrors a
# fire-and-forget event emitter; we never await its result inside hot paths
# so a slow ws_manager cannot stall command execution.
EventBroadcaster = Callable[[str, dict[str, Any]], Awaitable[None]]
_broadcaster: EventBroadcaster | None = None


def configure_terminal_event_broadcaster(broadcaster: EventBroadcaster | None) -> None:
    """Wire an emitter (typically `ConnectionManager.broadcast`) for lifecycle events."""
    global _broadcaster
    _broadcaster = broadcaster


# Completion-handler hook for background runs. Same shape as the legacy
# `configure_runtime_job_completion_callback` so the existing
# `_handle_runtime_job_completed` in app/main.py can be reused verbatim:
# the agent gets a wakeup turn + an interjection system message.
CompletionHandler = Callable[[str, dict[str, Any], str, str], Awaitable[None]]
_completion_handler: CompletionHandler | None = None


def configure_terminal_completion_handler(handler: CompletionHandler | None) -> None:
    """Wire the callback fired when a background terminal command finishes.

    Signature: ``handler(session_id, job_dict, stdout_tail, stderr_tail)``.
    Typically wired at app startup to ``_handle_runtime_job_completed`` —
    that path queues a wakeup so the agent gets a new turn with the result.
    """
    global _completion_handler
    _completion_handler = handler


def _q(value: str) -> str:
    """POSIX single-quote escape for shell interpolation."""
    return "'" + value.replace("'", "'\\''") + "'"


@dataclasses.dataclass
class TerminalRecord:
    terminal_id: str
    tmux_name: str
    pipe_log_path: str
    workspace_path: str
    session_user: str
    created_by: str          # "agent" | "user"
    created_at: float
    last_used_at: float
    label: str | None = None
    auto: bool = False       # auto-allocated by the parallel-call pre-pass
    last_command: str | None = None
    last_cwd: str | None = None

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "terminal_id": self.terminal_id,
            "label": self.label,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "auto": self.auto,
            "last_command": self.last_command,
            "last_cwd": self.last_cwd,
        }


class TerminalBlockedError(RuntimeError):
    """Raised when a foreground process owned by the user blocks an agent send."""

    def __init__(self, reason: str, *, current_command: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.current_command = current_command


class TerminalUnavailableError(RuntimeError):
    """Raised when the guest is missing tmux or the tmux session cannot be reached.

    Surfaces a structured failure to the tool handler instead of silently
    hanging the exit-file poll for the full timeout when tmux returns
    non-zero. The handler converts this into a normal tool result that the
    agent can react to.
    """

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class TerminalManager:
    """Singleton coordinator for tmux-backed terminals across all chat sessions."""

    def __init__(self) -> None:
        # session_id -> { terminal_id -> TerminalRecord }
        self._terminals: dict[str, dict[str, TerminalRecord]] = defaultdict(dict)
        # (session_id, terminal_id) -> Lock that serializes agent send-keys + capture
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # (session_id, terminal_id) -> set of attached WebSockets
        self._attachments: dict[tuple[str, str], set[Any]] = defaultdict(set)
        self._global_lock = asyncio.Lock()
        # Fire-and-forget watchers for background commands. We keep handles so
        # lifespan shutdown can cancel them deterministically — without this,
        # an in-flight `_watch_background_completion` poll loop keeps an
        # asyncssh channel busy and uvicorn waits for it indefinitely on
        # reload. Entries self-evict via a done_callback below.
        self._background_tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_terminals(self, session_id: UUID | str) -> list[TerminalRecord]:
        key = str(session_id)
        return list(self._terminals.get(key, {}).values())

    def descriptors_for(self, session_id: UUID | str) -> list[dict[str, Any]]:
        return [record.to_descriptor() for record in self.list_terminals(session_id)]

    async def run_command(
        self,
        *,
        runtime: RuntimeInstance,
        session_id: UUID | str,
        terminal_id: str,
        command: str,
        timeout: int,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        label_hint: str | None = None,
        created_by: str = "agent",
        auto: bool = False,
    ) -> RuntimeExecResult:
        """Execute a command inside the named tmux session, capturing stdout/stderr/exit reliably."""
        session_key = str(session_id)
        terminal_id = self._validate_terminal_id(terminal_id)
        timeout = max(1, timeout or _DEFAULT_RUN_TIMEOUT_SECONDS)
        ssh = self._extract_ssh(runtime)
        session_user = self._session_user(runtime)
        workspace = self._workspace(runtime)

        record = await self._ensure_terminal_locked(
            session_key=session_key,
            terminal_id=terminal_id,
            ssh=ssh,
            session_user=session_user,
            workspace=workspace,
            created_by=created_by,
            label_hint=label_hint,
            auto=auto,
        )

        lock = self._lock_for(session_key, terminal_id)
        async with lock:
            await self._refuse_if_foreground_busy(ssh, record)
            # Update last_command/last_cwd BEFORE running so the busy=True
            # broadcast carries the new command — the UI then shows the
            # currently-running thing in the pill tooltip / panel header.
            record.last_command = self._normalize_label(command)
            record.last_cwd = cwd
            await self._broadcast_busy(
                session_key,
                terminal_id,
                busy=True,
                last_command=record.last_command,
                last_cwd=record.last_cwd,
            )
            try:
                result = await self._send_and_capture(
                    ssh=ssh,
                    record=record,
                    command=command,
                    env=env or {},
                    cwd=cwd,
                    timeout=timeout,
                )
            finally:
                record.last_used_at = time.time()
                # Label is only set if it isn't already, and only as a fallback
                # for auto-allocated terminals (named ones derive their label
                # from terminal_id on the frontend).
                if record.label is None and label_hint and terminal_id.startswith("auto-"):
                    record.label = self._normalize_label(label_hint)
                await self._broadcast_busy(
                    session_key,
                    terminal_id,
                    busy=False,
                    last_command=record.last_command,
                    last_cwd=record.last_cwd,
                )
        return result

    async def run_command_background(
        self,
        *,
        runtime: RuntimeInstance,
        session_id: UUID | str,
        terminal_id: str,
        command: str,
        timeout: int,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        label_hint: str | None = None,
        created_by: str = "agent",
        auto: bool = False,
    ) -> dict[str, Any]:
        """Spawn a command in the terminal and return immediately with a handle.

        Unlike ``run_command`` this does NOT block on completion. The command
        is dispatched into its tmux pane just like a foreground run, then a
        background asyncio task watches for the next OSC 133 D marker. When
        the command finishes the configured completion handler fires (see
        ``configure_terminal_completion_handler``) — typically a wakeup that
        queues a fresh agent turn carrying stdout/stderr/exit code.

        The agent gets back ``{ok, background, terminal_id, started_at}``.
        It should NOT poll; the notification is the contract.
        """
        session_key = str(session_id)
        terminal_id = self._validate_terminal_id(terminal_id)
        timeout = max(1, timeout or _DEFAULT_RUN_TIMEOUT_SECONDS)
        ssh = self._extract_ssh(runtime)
        session_user = self._session_user(runtime)
        workspace = self._workspace(runtime)

        record = await self._ensure_terminal_locked(
            session_key=session_key,
            terminal_id=terminal_id,
            ssh=ssh,
            session_user=session_user,
            workspace=workspace,
            created_by=created_by,
            label_hint=label_hint,
            auto=auto,
        )

        # Acquire the per-terminal lock for the duration of *sending* (so we
        # serialize against any other agent calls into this terminal) but
        # release it before awaiting completion — the lock is about ordering
        # send-keys, not about gatekeeping the entire run.
        lock = self._lock_for(session_key, terminal_id)
        async with lock:
            await self._refuse_if_foreground_busy(ssh, record)
            record.last_command = self._normalize_label(command)
            record.last_cwd = cwd
            await self._broadcast_busy(
                session_key,
                terminal_id,
                busy=True,
                last_command=record.last_command,
                last_cwd=record.last_cwd,
            )
            try:
                initial_offset = await self._send_to_pane(
                    ssh=ssh,
                    record=record,
                    command=command,
                    env=env or {},
                    cwd=cwd,
                )
            except Exception:
                # Failed to send — clear busy so the pill doesn't lie.
                await self._broadcast_busy(
                    session_key,
                    terminal_id,
                    busy=False,
                    last_command=record.last_command,
                    last_cwd=record.last_cwd,
                )
                raise

        started_at = datetime.now(UTC).isoformat()
        # Schedule the completion watcher; it owns the busy=False broadcast
        # and (if configured) the notification handler invocation. The handle
        # is tracked so app shutdown can cancel it deterministically.
        self._spawn_background_task(
            self._watch_background_completion(
                ssh=ssh,
                record=record,
                session_key=session_key,
                command=command,
                initial_offset=initial_offset,
                timeout=timeout,
                started_at=started_at,
            )
        )
        return {
            "ok": True,
            "background": True,
            "terminal_id": terminal_id,
            "started_at": started_at,
        }

    async def read_pane_tail(
        self,
        *,
        runtime: RuntimeInstance,
        session_id: UUID | str,
        terminal_id: str,
        tail_bytes: int = 8_000,
    ) -> str:
        """Return the recent bytes from a terminal's pipe-pane log, ANSI-stripped.

        Used by the ``runtime.terminal_read`` tool when an agent has a real
        reason to peek at intermediate progress. Discouraged for routine
        polling — the completion notification is the right path for that.
        """
        session_key = str(session_id)
        terminal_id = self._validate_terminal_id(terminal_id)
        async with self._global_lock:
            record = self._terminals.get(session_key, {}).get(terminal_id)
        if record is None:
            raise ValueError(f"Unknown terminal_id {terminal_id!r} for session {session_key}")
        ssh = self._extract_ssh(runtime)
        # Same permission trick we use in the agent capture path: the
        # pipe-pane log lives inside the session user's 0700 workspace, so
        # any read has to happen `sudo -u` as that user.
        bytes_count = max(256, min(int(tail_bytes), 200_000))
        try:
            result = await ssh.run(
                f"sudo -u {_q(record.session_user)} bash -lc "
                f"{_q('test -f ' + _q(record.pipe_log_path) + ' && tail -c ' + str(bytes_count) + ' ' + _q(record.pipe_log_path) + ' || true')}",
                timeout=15,
            )
        except Exception:
            return ""
        raw = result.stdout or ""
        if isinstance(raw, str):
            raw_bytes = raw.encode("utf-8", errors="replace")
        else:
            raw_bytes = raw
        return _ANSI_PATTERN.sub(b"", raw_bytes).decode("utf-8", errors="replace")

    async def _watch_background_completion(
        self,
        *,
        ssh: SSHClient,
        record: TerminalRecord,
        session_key: str,
        command: str,
        initial_offset: int,
        timeout: int,
        started_at: str,
    ) -> None:
        """Wait for OSC 133 D and fire the completion handler."""
        try:
            result = await self._await_command_complete(ssh, record, initial_offset, timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "background watcher crashed terminal=%s: %s", record.terminal_id, exc
            )
            result = RuntimeExecResult(exit_status=-1, stdout="", stderr=f"watcher_error: {exc}")
        finally:
            record.last_used_at = time.time()
            await self._broadcast_busy(
                session_key,
                record.terminal_id,
                busy=False,
                last_command=record.last_command,
                last_cwd=record.last_cwd,
            )

        handler = _completion_handler
        if handler is None:
            return

        ended_at = datetime.now(UTC).isoformat()
        # Shape the job dict so it matches what the legacy completion handler
        # in main.py expects (id, status, returncode, command). We use the
        # terminal_id as the "job id" — there's no separate identifier in the
        # unified model.
        job_dict: dict[str, Any] = {
            "id": record.terminal_id,
            "status": "completed" if result.exit_status == 0 else "failed",
            "returncode": result.exit_status,
            "command": command,
            "terminal_id": record.terminal_id,
            "started_at": started_at,
            "ended_at": ended_at,
        }
        try:
            await handler(session_key, job_dict, result.stdout, result.stderr)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "background completion handler failed terminal=%s: %s",
                record.terminal_id,
                exc,
            )

    async def _send_to_pane(
        self,
        *,
        ssh: SSHClient,
        record: TerminalRecord,
        command: str,
        env: dict[str, str],
        cwd: str | None,
    ) -> int:
        """Send the visible command into the pane and return the pipe-log offset just before it.

        Factored out so foreground (``_send_and_capture``) and background
        (``_watch_background_completion``) flows share the same sender —
        diverging only in how they wait for the OSC 133 D marker afterward.
        """
        visible_command = self._build_visible_command(command, env, cwd)
        initial_offset = await self._pipe_log_size(ssh, record)
        await ssh.run(
            f"sudo -u {_q(record.session_user)} tmux send-keys -t {_q(record.tmux_name)} C-u",
            timeout=10,
        )
        await ssh.run(
            f"sudo -u {_q(record.session_user)} tmux send-keys -t {_q(record.tmux_name)} -l {_q(visible_command)}",
            timeout=15,
        )
        await ssh.run(
            f"sudo -u {_q(record.session_user)} tmux send-keys -t {_q(record.tmux_name)} Enter",
            timeout=10,
        )
        return initial_offset

    async def attach_ws(
        self,
        *,
        runtime: RuntimeInstance,
        session_id: UUID | str,
        terminal_id: str,
        websocket: WebSocket,
    ) -> None:
        """PTY proxy: byte-stream the tmux pane to/from the WebSocket.

        Opens a single long-lived SSH channel with a PTY allocated, running
        ``tmux attach-session -t <name>``. Every byte the user types on the
        WebSocket goes straight into the PTY's stdin; every byte the pane
        emits comes out of the PTY's stdout as a binary WS frame. Resize is
        forwarded through SSH's native window-change message via
        ``process.change_terminal_size``.

        This is how ttyd / gotty / VS Code's terminal / Cursor's terminal
        do it. No per-keystroke SSH overhead, no log tailing, full ANSI
        fidelity. The user gets sub-frame typing latency and ``vim`` /
        ``htop`` / curses apps Just Work.

        Agent capture (``_send_and_capture``) is a separate path via
        ``tmux send-keys`` + OSC 133 reading on pipe-pane — unchanged.
        """
        session_key = str(session_id)
        terminal_id = self._validate_terminal_id(terminal_id)
        ssh = self._extract_ssh(runtime)
        session_user = self._session_user(runtime)
        workspace = self._workspace(runtime)

        logger.info("attach_ws start (PTY) session=%s terminal_id=%s", session_key, terminal_id)
        record = await self._ensure_terminal_locked(
            session_key=session_key,
            terminal_id=terminal_id,
            ssh=ssh,
            session_user=session_user,
            workspace=workspace,
            created_by="user",
            label_hint=None,
            auto=False,
        )
        logger.info(
            "attach_ws record_ready session=%s terminal_id=%s tmux=%s",
            session_key,
            terminal_id,
            record.tmux_name,
        )

        attachment_key = (session_key, terminal_id)
        self._attachments[attachment_key].add(websocket)

        attach_cmd = (
            f"sudo -u {_q(record.session_user)} "
            f"tmux attach-session -t {_q(record.tmux_name)}"
        )
        process = None
        out_task: asyncio.Task | None = None
        try:
            # `encoding=None` makes stdin/stdout pure byte streams — important
            # because terminal escape sequences and 8-bit UTF-8 must pass
            # through unmolested. `term_size` is just the initial size; the
            # client sends a real resize control frame on connect.
            process = await ssh.create_process(
                attach_cmd,
                term_type="xterm-256color",
                term_size=(80, 24),
                encoding=None,
            )

            async def out_pump() -> None:
                """Bytes from the guest PTY → WS as binary frames."""
                while True:
                    try:
                        chunk = await process.stdout.read(4096)
                    except Exception:  # noqa: BLE001
                        return
                    if not chunk:
                        return
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8", errors="replace")
                    try:
                        await websocket.send_bytes(chunk)
                    except Exception:
                        return

            out_task = asyncio.create_task(out_pump())

            while True:
                try:
                    msg = await websocket.receive()
                except WebSocketDisconnect:
                    break
                except Exception:
                    break
                msg_type = msg.get("type")
                if msg_type == "websocket.disconnect":
                    break
                if msg_type != "websocket.receive":
                    continue

                text = msg.get("text")
                raw = msg.get("bytes")

                # Resize control frame is JSON, not raw input — short-circuit
                # before we treat it as keystrokes.
                if text is not None and text.startswith("{"):
                    try:
                        ctrl = json.loads(text)
                    except Exception:
                        ctrl = None
                    if isinstance(ctrl, dict) and ctrl.get("type") == "resize":
                        cols = max(20, int(ctrl.get("cols") or _DEFAULT_TMUX_COLS))
                        rows = max(5, int(ctrl.get("rows") or _DEFAULT_TMUX_ROWS))
                        try:
                            process.change_terminal_size(cols, rows)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("PTY resize failed: %s", exc)
                        continue

                # Everything else is keystroke bytes for the PTY.
                if raw is not None:
                    data = raw
                elif text is not None:
                    data = text.encode("utf-8", errors="replace")
                else:
                    continue
                if not data:
                    continue
                try:
                    process.stdin.write(data)
                except Exception:
                    break
        finally:
            if out_task is not None:
                out_task.cancel()
                try:
                    await out_task
                except (asyncio.CancelledError, Exception):
                    pass
            if process is not None:
                # Detach the tmux client cleanly; the tmux session and its
                # bash inside KEEP running for other attachees and future
                # agent commands.
                try:
                    process.terminate()
                except Exception:
                    pass
                try:
                    await process.wait()
                except Exception:
                    pass
            self._attachments[attachment_key].discard(websocket)
            logger.info("attach_ws closed session=%s terminal_id=%s", session_key, terminal_id)

    def _spawn_background_task(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        """Track every fire-and-forget task so shutdown can cancel them.

        Returning the task lets callers introspect/cancel individually if they
        ever want to. The done-callback removes the entry once the task
        finishes naturally — no leak even under steady-state load.
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def shutdown(self, *, timeout: float = 5.0) -> None:
        """Cancel every in-flight background watcher and wait for them to drain.

        Called from the FastAPI lifespan `finally` block. Bounded by `timeout`
        so a watcher stuck inside asyncssh can't wedge the whole reload — any
        task that doesn't honour cancellation within the deadline is left to
        the process exit. In dev that's harmless; the next process owns its
        own SSH conn and the guest tmux session is untouched.
        """
        if not self._background_tasks:
            return
        pending = list(self._background_tasks)
        for task in pending:
            task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=max(0.1, float(timeout)),
            )
        except asyncio.TimeoutError:
            logger.warning(
                "terminal_manager shutdown timed out after %.1fs; %d watcher(s) left to OS",
                timeout,
                sum(1 for t in pending if not t.done()),
            )

    async def terminate(
        self,
        session_id: UUID | str,
        terminal_id: str | None = None,
    ) -> None:
        """Kill tmux session(s) and forget any local state for them."""
        session_key = str(session_id)
        async with self._global_lock:
            records = self._terminals.get(session_key, {})
            if not records:
                return
            targets: list[TerminalRecord]
            if terminal_id is None:
                targets = list(records.values())
            else:
                tid = self._validate_terminal_id(terminal_id)
                target = records.get(tid)
                targets = [target] if target is not None else []

        if not targets:
            return

        # We need an SSH client to send kill-session. The QEMU runtime
        # provider is a singleton.
        try:
            runtime = await get_runtime().ensure(session_key)
            ssh = self._extract_ssh(runtime)
        except Exception as exc:  # noqa: BLE001
            logger.warning("terminal teardown could not acquire runtime: %s", exc)
            ssh = None

        for record in targets:
            if ssh is not None:
                try:
                    await ssh.run(
                        f"sudo -u {_q(record.session_user)} tmux kill-session -t {_q(record.tmux_name)} 2>/dev/null || true",
                        timeout=10,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("tmux kill-session failed for %s: %s", record.tmux_name, exc)
            async with self._global_lock:
                self._terminals.get(session_key, {}).pop(record.terminal_id, None)
                self._locks.pop((session_key, record.terminal_id), None)
            await self._emit_event(
                "terminal_closed",
                {
                    "session_id": session_key,
                    "terminal_id": record.terminal_id,
                },
            )

    async def rehydrate(
        self,
        *,
        runtime: RuntimeInstance,
        session_id: UUID | str,
    ) -> list[TerminalRecord]:
        """After a backend restart, ask tmux which of our sessions still exist."""
        session_key = str(session_id)
        prefix = self._tmux_prefix(session_key)
        ssh = self._extract_ssh(runtime)
        session_user = self._session_user(runtime)
        workspace = self._workspace(runtime)

        try:
            result = await ssh.run(
                f"sudo -u {_q(session_user)} tmux list-sessions -F '#{{session_name}}' 2>/dev/null || true",
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("tmux list-sessions failed during rehydrate: %s", exc)
            return []

        names = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        existing = {record.tmux_name: record for record in self._terminals.get(session_key, {}).values()}
        recovered: list[TerminalRecord] = []
        now = time.time()
        for name in names:
            if not name.startswith(prefix):
                continue
            if name in existing:
                recovered.append(existing[name])
                continue
            terminal_id = name[len(prefix):] or _DEFAULT_TERMINAL_ID
            record = TerminalRecord(
                terminal_id=terminal_id,
                tmux_name=name,
                pipe_log_path=self._pipe_log_path(workspace, terminal_id),
                workspace_path=workspace,
                session_user=session_user,
                created_by="agent",
                created_at=now,
                last_used_at=now,
                label=None,
                auto=False,
            )
            async with self._global_lock:
                self._terminals[session_key][terminal_id] = record
            recovered.append(record)
        return recovered

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _lock_for(self, session_key: str, terminal_id: str) -> asyncio.Lock:
        key = (session_key, terminal_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _validate_terminal_id(self, terminal_id: str | None) -> str:
        if terminal_id is None or terminal_id == "":
            return _DEFAULT_TERMINAL_ID
        candidate = str(terminal_id).strip()
        if not _TERMINAL_ID_PATTERN.match(candidate):
            raise ValueError(
                f"Invalid terminal_id {candidate!r}: must match {_TERMINAL_ID_PATTERN.pattern}"
            )
        return candidate

    def _terminal_session(self, runtime: RuntimeInstance) -> RuntimeTerminalSession:
        terminal = getattr(runtime, "terminal", None)
        if terminal is None:
            provider = str(getattr(runtime, "metadata", {}).get("provider") or "unknown")
            raise TerminalUnavailableError(
                "terminal_not_supported",
                detail=f"Runtime provider '{provider}' does not expose an SSH/tmux terminal session.",
            )
        return terminal

    def _extract_ssh(self, runtime: RuntimeInstance) -> SSHClient:
        return self._terminal_session(runtime).ssh  # type: ignore[return-value]

    def _session_user(self, runtime: RuntimeInstance) -> str:
        user = self._terminal_session(runtime).session_user
        if not isinstance(user, str) or not user:
            raise TerminalUnavailableError(
                "terminal_session_invalid",
                detail="Runtime terminal session is missing a session user.",
            )
        return user

    def _workspace(self, runtime: RuntimeInstance) -> str:
        path = self._terminal_session(runtime).workspace_path
        if not isinstance(path, str) or not path:
            raise TerminalUnavailableError(
                "terminal_session_invalid",
                detail="Runtime terminal session is missing a workspace path.",
            )
        return path.rstrip("/")

    def _tmux_prefix(self, session_key: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]", "", session_key)[:12] or "anon"
        return f"sentinel_{slug}_"

    def _tmux_name(self, session_key: str, terminal_id: str) -> str:
        return f"{self._tmux_prefix(session_key)}{terminal_id}"

    def _pipe_log_path(self, workspace: str, terminal_id: str) -> str:
        return f"{workspace}/.runtime/term/{terminal_id}/pane.log"

    def _normalize_label(self, value: str | None) -> str | None:
        if not value:
            return None
        text = value.strip().splitlines()[0].strip()
        if not text:
            return None
        return text[:40]

    async def _ensure_terminal_locked(
        self,
        *,
        session_key: str,
        terminal_id: str,
        ssh: SSHClient,
        session_user: str,
        workspace: str,
        created_by: str,
        label_hint: str | None,
        auto: bool,
    ) -> TerminalRecord:
        async with self._global_lock:
            existing = self._terminals.get(session_key, {}).get(terminal_id)
        if existing is not None:
            # If a previous boot of the backend ran with the same terminal,
            # the tmux session may still exist. If it doesn't (e.g. user
            # killed it via `exit`), recreate.
            if await self._tmux_has_session(ssh, existing):
                return existing

        tmux_name = self._tmux_name(session_key, terminal_id)
        pipe_log = self._pipe_log_path(workspace, terminal_id)
        record = TerminalRecord(
            terminal_id=terminal_id,
            tmux_name=tmux_name,
            pipe_log_path=pipe_log,
            workspace_path=workspace,
            session_user=session_user,
            created_by=created_by,
            created_at=time.time(),
            last_used_at=time.time(),
            label=self._normalize_label(label_hint),
            auto=auto,
        )

        await self._create_tmux_session(ssh, record)
        async with self._global_lock:
            self._terminals[session_key][terminal_id] = record
        await self._emit_event(
            "terminal_opened",
            {
                "session_id": session_key,
                "terminal_id": record.terminal_id,
                "label": record.label,
                "created_by": record.created_by,
                "auto": record.auto,
            },
        )
        return record

    async def _tmux_has_session(self, ssh: SSHClient, record: TerminalRecord) -> bool:
        try:
            result = await ssh.run(
                f"sudo -u {_q(record.session_user)} tmux has-session -t {_q(record.tmux_name)} 2>/dev/null && echo yes || echo no",
                timeout=10,
            )
        except Exception:
            return False
        return (result.stdout or "").strip().endswith("yes")

    async def _create_tmux_session(self, ssh: SSHClient, record: TerminalRecord) -> None:
        # Fail fast if the guest image lacks tmux entirely — otherwise the
        # downstream send-keys calls all exit non-zero and the exit-file poll
        # would hang waiting for output the wrapper never wrote. Distinguishing
        # "tmux missing" from "tmux session crashed" produces a clearer error.
        try:
            probe = await ssh.run("command -v tmux >/dev/null 2>&1 && echo present || echo missing", timeout=10)
        except Exception as exc:  # noqa: BLE001
            raise TerminalUnavailableError(
                "guest_unreachable",
                detail=f"could not probe guest for tmux: {exc}",
            ) from exc
        if "present" not in (probe.stdout or ""):
            raise TerminalUnavailableError(
                "tmux_not_installed",
                detail="`tmux` is not on PATH inside the runtime guest. Rebuild the base image with the updated provision script.",
            )

        rcfile_path = f"{record.workspace_path}/.runtime/term/{record.terminal_id}/bashrc"
        # Make sure the workspace directory tree exists, the pane log file
        # is created, and a minimal bashrc lands at a known path. The session
        # user is created with `useradd -M` so /etc/skel/.bashrc is never
        # copied — without our own rcfile the shell starts with no color
        # aliases (`ls`, `grep`, `diff` all monochrome) and a bare PS1.
        # Drop a small rc into the workspace and `bash --rcfile <path> -i`
        # below so every new pane gets readable colored output.
        rcfile_b64 = base64.b64encode(_SENTINEL_BASHRC.encode("utf-8")).decode("ascii")
        prep = (
            f"mkdir -p {_q(record.workspace_path)}/.runtime/term/{_q(record.terminal_id)} && "
            f"touch {_q(record.pipe_log_path)} && "
            f"printf %s {_q(rcfile_b64)} | base64 -d > {_q(rcfile_path)} && "
            f"chown -R {_q(record.session_user)}:{_q(record.session_user)} "
            f"{_q(record.workspace_path)}/.runtime"
        )
        result = await ssh.run(f"sudo bash -lc {_q(prep)}", timeout=15)
        if result.exit_status != 0:
            detail = (result.stderr or result.stdout or "").strip()[:500]
            logger.warning(
                "guest_prep failed terminal=%s user=%s workspace=%s exit=%d detail=%s",
                record.terminal_id,
                record.session_user,
                record.workspace_path,
                result.exit_status,
                detail or "(empty)",
            )
            raise TerminalUnavailableError(
                "guest_prep_failed",
                detail=detail,
            )

        # `tmux new-session -d` returns immediately; the pane runs an
        # interactive bash that sources our rcfile so colors / prompt are set
        # up. `-e TERM=...` tells programs inside the pane to emit 256-color
        # ANSI; without it many tools fall back to mono. `-e HOME=<workspace>`
        # is the trick that lets per-command `runtime.user` calls stay
        # wrapper-free: the shell already has the right HOME, so the handler
        # doesn't need to re-export it on every command.
        bash_invocation = f"bash --rcfile {_q(rcfile_path)} -i"
        new_session = (
            f"sudo -u {_q(record.session_user)} tmux new-session "
            f"-d -s {_q(record.tmux_name)} "
            f"-x {_DEFAULT_TMUX_COLS} -y {_DEFAULT_TMUX_ROWS} "
            f"-c {_q(record.workspace_path)} "
            f"-e TERM=xterm-256color "
            f"-e COLORTERM=truecolor "
            f"-e HOME={_q(record.workspace_path)} "
            f"{_q(bash_invocation)}"
        )
        result = await ssh.run(new_session, timeout=15)
        if result.exit_status != 0:
            raise TerminalUnavailableError(
                "tmux_new_session_failed",
                detail=(result.stderr or result.stdout or "").strip()[:500],
            )

        # remain-on-exit lets us detect "user typed exit" without losing the
        # pane; history-limit bumps scrollback well above tmux's 2000 default
        # so long builds still fit; pipe-pane sinks every byte to disk so the
        # WS attach handler can tail without needing a live tmux client;
        # mouse on makes wheel events scroll tmux's scrollback (copy mode)
        # instead of falling through to bash as Up/Down arrow keystrokes —
        # otherwise scrolling in the browser steps through bash history.
        post = (
            f"sudo -u {_q(record.session_user)} tmux set-option -t {_q(record.tmux_name)} remain-on-exit on && "
            f"sudo -u {_q(record.session_user)} tmux set-option -t {_q(record.tmux_name)} history-limit {_TMUX_HISTORY_LIMIT} && "
            f"sudo -u {_q(record.session_user)} tmux set-option -t {_q(record.tmux_name)} mouse on && "
            f"sudo -u {_q(record.session_user)} tmux pipe-pane -t {_q(record.tmux_name)} -o "
            f"\"cat >> {_q(record.pipe_log_path)}\""
        )
        result = await ssh.run(post, timeout=15)
        if result.exit_status != 0:
            # Non-fatal: the session itself is alive, the options just didn't
            # stick. Log and continue; scrollback may be small, pipe-pane may
            # not capture for the WS attach. Surfacing a hard error here would
            # break agent commands that would otherwise succeed.
            logger.warning(
                "tmux post-create configuration failed for %s: %s",
                record.tmux_name,
                (result.stderr or result.stdout or "").strip()[:500],
            )

    async def _refuse_if_foreground_busy(
        self,
        ssh: SSHClient,
        record: TerminalRecord,
    ) -> None:
        """Refuse to send-keys when the user is running a foreground process.

        tmux's `pane_current_command` exposes the foreground process the pane's
        shell is waiting on. If it's anything other than the login shell, the
        user has something running (vim, top, a build, etc.) and our send-keys
        would either get queued behind their input or interrupt them. Bail with
        a structured error so the agent can pick another terminal or retry.
        """
        cmd = (
            f"sudo -u {_q(record.session_user)} tmux display-message "
            f"-p -t {_q(record.tmux_name)} '#{{pane_current_command}}'"
        )
        try:
            result = await ssh.run(cmd, timeout=10)
        except Exception:
            return
        current = (result.stdout or "").strip()
        if current and current not in {"bash", "sh", "zsh", "fish", "dash"}:
            raise TerminalBlockedError(
                "user_foreground_process",
                current_command=current,
            )

    async def _send_and_capture(
        self,
        *,
        ssh: SSHClient,
        record: TerminalRecord,
        command: str,
        env: dict[str, str],
        cwd: str | None,
        timeout: int,
    ) -> RuntimeExecResult:
        """Foreground execution: send the command, await the OSC 133 D marker.

        Shares the send half with ``run_command_background`` via
        ``_send_to_pane`` so the two paths can't drift.
        """
        initial_offset = await self._send_to_pane(
            ssh=ssh, record=record, command=command, env=env, cwd=cwd,
        )
        return await self._await_command_complete(ssh, record, initial_offset, timeout)

    def _build_visible_command(
        self,
        command: str,
        env: dict[str, str] | None,
        cwd: str | None,
    ) -> str:
        """Compose the human-readable shell command to send to the pane.

        - No env / no cwd → send the agent's command verbatim. Maximum
          readability: the user just sees ``$ <command>``.
        - cwd only → wrap in ``(cd <path> && <command>)``. Scoped to that
          one invocation so the pane's persistent shell state isn't changed.
        - env only → wrap in ``(export K=V; <command>)`` so the variables
          stay scoped to that invocation regardless of pipes/subshells
          inside the command itself.
        - both → both, in one subshell.

        Use a *real* ``cd`` / ``export`` shell_command (no env/cwd args) when
        the agent wants the change to stick — that's documented on the tool.
        """
        has_env = bool(env)
        has_cwd = bool(cwd)
        if not has_env and not has_cwd:
            return command
        parts: list[str] = []
        if has_cwd:
            parts.append(f"cd {_q(cwd)}")
        if has_env:
            for key, value in env.items():
                if not isinstance(key, str) or not key:
                    continue
                parts.append(f"export {key}={_q(str(value))}")
        # cd is joined with `&&` (we want to bail if cd fails); exports are
        # safe with `;` (they don't fail under normal conditions).
        if has_cwd:
            prefix = parts[0] + " && " + "; ".join(parts[1:] + [command])
        else:
            prefix = "; ".join(parts + [command])
        return f"({prefix})"

    async def _pipe_log_size(self, ssh: SSHClient, record: TerminalRecord) -> int:
        """Current byte offset of the pipe-pane log; used as the start of our read window."""
        try:
            result = await ssh.run(
                f"sudo -u {_q(record.session_user)} bash -lc "
                f"{_q('stat -c %s ' + _q(record.pipe_log_path) + ' 2>/dev/null || echo 0')}",
                timeout=10,
            )
        except Exception:
            return 0
        text = (result.stdout or "").strip()
        try:
            return int(text)
        except (ValueError, TypeError):
            return 0

    async def _await_command_complete(
        self,
        ssh: SSHClient,
        record: TerminalRecord,
        since_offset: int,
        timeout: int,
    ) -> RuntimeExecResult:
        """Poll the pipe-pane log for the next OSC 133 D marker after `since_offset`."""
        deadline = asyncio.get_running_loop().time() + timeout
        last_chunk = b""
        while True:
            try:
                result = await ssh.run(
                    f"sudo -u {_q(record.session_user)} bash -lc "
                    f"{_q('tail -c +' + str(since_offset + 1) + ' ' + _q(record.pipe_log_path) + ' 2>/dev/null || true')}",
                    timeout=10,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("pipe-pane log read failed: %s", exc)
                result = None

            if result is not None:
                stdout = result.stdout or ""
                last_chunk = stdout.encode("utf-8", errors="replace") if isinstance(stdout, str) else stdout
                match = _OSC_D_PATTERN.search(last_chunk)
                if match:
                    # match.group(1) is bytes (pattern is rb"..."); decode and
                    # parse defensively. Missing or non-numeric → -1 fallback.
                    raw_code = match.group(1).decode("ascii", errors="replace") if match.group(1) else ""
                    exit_code = int(raw_code) if raw_code and raw_code.lstrip("-").isdigit() else -1
                    return self._parse_output(last_chunk[: match.start()], exit_code)

            if asyncio.get_running_loop().time() >= deadline:
                # Timed out — return whatever we have so far, with a marker
                # in stderr so the agent can react.
                return RuntimeExecResult(
                    exit_status=-1,
                    stdout=self._parse_output(last_chunk, -1).stdout,
                    stderr="[command did not finish within timeout]",
                )
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    def _parse_output(self, raw: bytes, exit_code: int) -> RuntimeExecResult:
        """Turn the pipe-pane bytes for one command into the agent-facing result.

        The bytes look like ``<echoed-command-line>\\n<output>``: bash echoes
        every keystroke as we typed via send-keys, then the command runs and
        writes its output. Slice off the first line (the echo) and strip
        ANSI/OSC escapes so the agent gets clean plain text.
        """
        # Strip ANSI/OSC escapes first — including stray OSC 133 A/B markers
        # bash may have written into the log between our send and the next
        # prompt.
        cleaned = _ANSI_PATTERN.sub(b"", raw)
        text = cleaned.decode("utf-8", errors="replace")
        # The first line is the echo of our own command. Drop it.
        newline = text.find("\n")
        if newline >= 0:
            output = text[newline + 1 :]
        else:
            output = ""
        # Trim trailing whitespace / the partial start of the next PS1 line.
        # The next-prompt characters can leak in if the regex slice landed
        # mid-prompt; rstrip is safe because no command's *real* trailing
        # whitespace is semantically meaningful for the agent.
        return RuntimeExecResult(
            exit_status=exit_code,
            stdout=output.rstrip(),
            stderr="",
        )

    async def _broadcast_busy(
        self,
        session_key: str,
        terminal_id: str,
        *,
        busy: bool,
        last_command: str | None = None,
        last_cwd: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "session_id": session_key,
            "terminal_id": terminal_id,
            "busy": busy,
        }
        if last_command is not None:
            payload["last_command"] = last_command
        if last_cwd is not None:
            payload["last_cwd"] = last_cwd
        await self._emit_event("terminal_busy", payload)

    async def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        broadcaster = _broadcaster
        if broadcaster is None:
            return
        try:
            await broadcaster(payload["session_id"], {"type": event_type, **payload})
        except Exception as exc:  # noqa: BLE001
            logger.debug("terminal event broadcast failed: %s", exc)


# ----------------------------------------------------------------------
# Singleton accessor
# ----------------------------------------------------------------------

_singleton: TerminalManager | None = None


def get_terminal_manager() -> TerminalManager:
    global _singleton
    if _singleton is None:
        _singleton = TerminalManager()
    return _singleton
