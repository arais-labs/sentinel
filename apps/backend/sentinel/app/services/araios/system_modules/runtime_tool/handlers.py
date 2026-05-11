"""Native module: runtime — shell command execution in persistent tmux terminals.

The whole module went through a unification: there is no separate "detached
job" path anymore. Every command runs in a tmux pane via TerminalManager,
and `background=true` is the difference between "wait for completion" and
"return a handle now, notification fires the agent later".

Legacy `runtime.jobs / job_status / job_logs / job_stop` are gone (hard
cut). Their replacements are `runtime.terminal_list` and
`runtime.terminal_read`, and `runtime.user(background=true)`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.database.database import AsyncSessionLocal
from app.services.runtime import get_runtime
from app.services.runtime.base import RuntimeExecResult
from app.services.runtime.session_runtime import (
    ensure_runtime_layout,
    mark_runtime_state,
    runtime_workspace_dir,
)
from app.services.runtime.terminal_manager import (
    TerminalBlockedError,
    TerminalUnavailableError,
    get_terminal_manager,
)
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.runtime_context import require_runtime_session_id

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_RUNTIME_EXEC_OUTPUT_CHARS = 50_000
_TERMINAL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
_DEFAULT_TERMINAL_ID = "0"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _truncate_runtime_exec_text(value: str | None) -> str:
    text = value or ""
    if len(text) <= _MAX_RUNTIME_EXEC_OUTPUT_CHARS:
        return text
    return f"{text[:_MAX_RUNTIME_EXEC_OUTPUT_CHARS]}\n...[truncated]"


def _command_requests_background_execution(command: str) -> bool:
    r"""Return True iff the command tries to background the inline path.

    Shell-syntax-aware scan: a `&` only counts as the background operator
    when bash itself would treat it as one. We track single-quoted,
    double-quoted, and heredoc state, and we skip `&&` / `>&` / `&>`.

    Handled:
      - single-quoted strings: literal until next `'`
      - double-quoted strings: literal until next unescaped `"`
      - heredocs (`<<DELIM`, `<<-DELIM`, `<<'DELIM'`, `<<"DELIM"`)
      - escaped chars outside quotes (e.g. `\&`)
      - logical AND (`&&`) and FD redirections (`2>&1`, `&>`, `>&`)
    """
    if not command or not command.strip():
        return False

    n = len(command)
    i = 0
    unquoted: list[str] = []
    heredoc_delim: str | None = None
    heredoc_strip_tabs = False

    while i < n:
        c = command[i]

        if heredoc_delim is not None:
            if c == "\n":
                line_end = command.find("\n", i + 1)
                if line_end < 0:
                    line_end = n
                line = command[i + 1 : line_end]
                checked = line.lstrip("\t") if heredoc_strip_tabs else line
                if checked == heredoc_delim:
                    heredoc_delim = None
                    heredoc_strip_tabs = False
                    i = line_end
                    continue
            i += 1
            continue

        if c == "'":
            j = command.find("'", i + 1)
            if j < 0:
                return False
            i = j + 1
            continue

        if c == '"':
            j = i + 1
            while j < n:
                if command[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if command[j] == '"':
                    break
                j += 1
            if j >= n:
                return False
            i = j + 1
            continue

        if c == "\\" and i + 1 < n:
            unquoted.append(" ")
            i += 2
            continue

        if c == "<" and i + 1 < n and command[i + 1] == "<":
            j = i + 2
            strip = False
            if j < n and command[j] == "-":
                strip = True
                j += 1
            quote = ""
            if j < n and command[j] in ("'", '"'):
                quote = command[j]
                j += 1
            delim_start = j
            while j < n and (command[j].isalnum() or command[j] == "_"):
                j += 1
            delim = command[delim_start:j]
            if quote and j < n and command[j] == quote:
                j += 1
            if delim:
                heredoc_delim = delim
                heredoc_strip_tabs = strip
                unquoted.append("<<")
                i = j
                continue

        if c == "&":
            prev = command[i - 1] if i > 0 else ""
            nxt = command[i + 1] if i + 1 < n else ""
            if nxt == "&":
                unquoted.append("&&")
                i += 2
                continue
            if prev == "&":
                i += 1
                continue
            if prev == ">" or nxt == ">":
                unquoted.append("&")
                i += 1
                continue
            return True

        unquoted.append(c)
        i += 1

    if re.search(r"\b(?:nohup|disown)\b", "".join(unquoted).lower()):
        return True
    return False


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
    background: bool,
    terminal_id: str,
    terminal_auto: bool,
) -> dict[str, Any]:
    """Run a command in the runtime — foreground (waits) or background (fire-and-forget).

    Foreground (background=False, the default): routes through
    ``TerminalManager.run_command`` and awaits completion before returning
    the full ``{returncode, stdout, stderr}`` payload.

    Background (background=True): routes through
    ``TerminalManager.run_command_background`` which returns immediately
    with a terminal handle; the agent gets a fresh wakeup turn carrying
    the result when the command finishes.

    Root commands (privilege='root') bypass the terminal entirely and run
    via direct SSH — they're gated by approval and traditionally one-shot.
    """
    runtime = await get_runtime().ensure(session_id)
    sandbox_workspace = runtime.workspace_path

    # Resolve cwd. We track two things separately:
    #   - sandbox_cwd: the "best guess" current directory for state tracking
    #     and the result payload (always populated, defaults to the workspace).
    #   - explicit_cwd: ONLY set when the agent passed a `cwd` parameter, in
    #     which case it must be applied as a per-command override. Defaults
    #     are never an override — the persistent shell already starts in the
    #     workspace, so re-`cd`'ing every call would just clutter the pane.
    sandbox_cwd = sandbox_workspace
    explicit_cwd: str | None = None
    if isinstance(cwd_raw, str) and cwd_raw.strip():
        requested = cwd_raw.strip()
        if Path(requested).is_absolute():
            if not requested.startswith(sandbox_workspace):
                raise ToolValidationError(
                    f"Field 'cwd' must stay within session workspace ({sandbox_workspace})"
                )
            sandbox_cwd = requested
        else:
            sandbox_cwd = f"{sandbox_workspace}/{requested}"
        explicit_cwd = sandbox_cwd

    # Env: only what the agent explicitly requested. Defaults (HOME, TERM,
    # ...) are baked into the tmux session at creation time so we don't have
    # to re-export them per command — keeping the pane readable.
    explicit_env: dict[str, str] = {}
    for key, value in env_payload.items():
        if not isinstance(key, str) or not key.strip():
            raise ToolValidationError("Environment variable keys must be non-empty strings")
        if value is None:
            continue
        if not isinstance(value, (str, int, float, bool)):
            raise ToolValidationError(
                f"Environment variable '{key}' must be string/number/boolean/null"
            )
        explicit_env[key] = str(value)

    # Root path: direct SSH, no terminal, full default env.
    if privilege != "user":
        env: dict[str, str] = {
            "HOME": sandbox_workspace,
            "PWD": sandbox_cwd,
            "TMPDIR": "/tmp",
            **explicit_env,
        }
        command_result_details: dict[str, Any] | None = None
        await mark_runtime_state(session_id, active=True, command=command_text, pid=None)
        try:
            try:
                result = await runtime.client.run(
                    command_text,
                    cwd=sandbox_cwd,
                    env=env,
                    timeout=timeout_seconds,
                    as_root=True,
                )
                timed_out = False
                timeout_hint = None
            except TimeoutError:
                timed_out = True
                timeout_hint = (
                    f"Command timed out after {timeout_seconds}s. "
                    "Use background=true for long-running commands."
                )
                result = RuntimeExecResult(exit_status=-1, stdout="", stderr="[timed out]")

            command_result_details = {
                "ok": not timed_out and result.exit_status == 0,
                "timed_out": timed_out,
                "returncode": result.exit_status,
                "stdout": _truncate_runtime_exec_text(result.stdout),
                "stderr": _truncate_runtime_exec_text(result.stderr),
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

    # User path: through the terminal.
    terminal_manager = get_terminal_manager()
    existed_before = any(
        rec.terminal_id == terminal_id
        for rec in terminal_manager.list_terminals(session_id)
    )

    if background:
        # Background fire-and-forget: spawn, return a handle, completion
        # will fire a wakeup notification via the configured handler.
        await mark_runtime_state(session_id, active=True, command=command_text, pid=None)
        try:
            try:
                bg_handle = await terminal_manager.run_command_background(
                    runtime=runtime,
                    session_id=session_id,
                    terminal_id=terminal_id,
                    command=command_text,
                    timeout=timeout_seconds,
                    env=explicit_env or None,
                    cwd=explicit_cwd,
                    label_hint=command_text,
                    created_by="agent",
                    auto=terminal_auto,
                )
            except TerminalBlockedError as exc:
                return {
                    "ok": False,
                    "background": True,
                    "terminal_id": terminal_id,
                    "terminal_auto": terminal_auto,
                    "terminal_blocked": {
                        "reason": exc.reason,
                        "current_command": exc.current_command,
                    },
                    "message": (
                        f"Cannot start background command in terminal {terminal_id!r}: "
                        f"user has a foreground process running ({exc.current_command or 'unknown'}). "
                        "Pick a different terminal_id."
                    ),
                    "session_id": str(session_id),
                    "workspace": sandbox_workspace,
                    "cwd": sandbox_cwd,
                    "privilege": privilege,
                }
            except TerminalUnavailableError as exc:
                return {
                    "ok": False,
                    "background": True,
                    "terminal_id": terminal_id,
                    "terminal_auto": terminal_auto,
                    "message": (
                        f"Terminal subsystem unavailable: {exc.reason}. "
                        f"{exc.detail or ''}"
                    ).strip(),
                    "session_id": str(session_id),
                    "workspace": sandbox_workspace,
                    "cwd": sandbox_cwd,
                    "privilege": privilege,
                }
            return {
                **bg_handle,
                "terminal_auto": terminal_auto,
                "terminal_created": not existed_before,
                "session_id": str(session_id),
                "workspace": sandbox_workspace,
                "cwd": sandbox_cwd,
                "privilege": privilege,
                "message": (
                    f"Background job started in terminal {terminal_id!r}. "
                    "Continue with other useful work in this turn, or end the turn. "
                    "DO NOT poll runtime.terminal_read waiting for completion — "
                    "you will receive a fresh agent turn with the result automatically."
                ),
            }
        finally:
            # mark_runtime_state for the FOREGROUND/background-spawn distinction
            # is fine here: background spawn returns immediately, so we flip
            # active back off. The actual completion is tracked by the watcher.
            await mark_runtime_state(session_id, active=False, command=command_text, pid=None)

    # Foreground: await full result.
    command_result_details_fg: dict[str, Any] | None = None
    await mark_runtime_state(session_id, active=True, command=command_text, pid=None)
    timeout_hint: str | None = None
    timed_out = False
    terminal_blocked: dict[str, Any] | None = None
    try:
        try:
            result = await terminal_manager.run_command(
                runtime=runtime,
                session_id=session_id,
                terminal_id=terminal_id,
                command=command_text,
                timeout=timeout_seconds,
                env=explicit_env or None,
                cwd=explicit_cwd,
                label_hint=command_text,
                created_by="agent",
                auto=terminal_auto,
            )
        except TerminalBlockedError as exc:
            terminal_blocked = {
                "reason": exc.reason,
                "current_command": exc.current_command,
            }
            result = RuntimeExecResult(
                exit_status=-1,
                stdout="",
                stderr=f"[terminal busy: {exc.current_command or 'foreground process'}]",
            )
        except TerminalUnavailableError as exc:
            result = RuntimeExecResult(
                exit_status=-1,
                stdout="",
                stderr=f"[terminal unavailable: {exc.reason}] {exc.detail or ''}".strip(),
            )
            timeout_hint = (
                f"Terminal subsystem unavailable: {exc.reason}. "
                f"{exc.detail or ''}"
            ).strip()
        except TimeoutError:
            timed_out = True
            timeout_hint = (
                f"Command timed out after {timeout_seconds}s. "
                "Use background=true for long-running commands."
            )
            result = RuntimeExecResult(exit_status=-1, stdout="", stderr="[timed out]")
        else:
            # `run_command` returns exit_status=-1 with a known stderr when the
            # internal poll deadline hits; surface that as a clean timeout.
            if result.exit_status == -1 and "did not finish within timeout" in (result.stderr or ""):
                timed_out = True
                timeout_hint = (
                    f"Command timed out after {timeout_seconds}s. "
                    "Use background=true for long-running commands."
                )

        stdout_text = _truncate_runtime_exec_text(result.stdout)
        stderr_text = _truncate_runtime_exec_text(result.stderr)
        ok = (
            not timed_out
            and terminal_blocked is None
            and result.exit_status == 0
        )

        command_result_details_fg = {
            "ok": ok,
            "timed_out": timed_out,
            "returncode": result.exit_status,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "message": timeout_hint,
            "privilege": privilege,
            "terminal_id": terminal_id,
            "terminal_auto": terminal_auto,
            "terminal_created": not existed_before and terminal_blocked is None,
        }
        if terminal_blocked is not None:
            command_result_details_fg["terminal_blocked"] = terminal_blocked
        return {
            **command_result_details_fg,
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
            action_details=command_result_details_fg,
        )


def _validate_terminal_id(raw: Any) -> str:
    if raw is None:
        return _DEFAULT_TERMINAL_ID
    if not isinstance(raw, str) or not raw.strip():
        raise ToolValidationError("Field 'terminal_id' must be a non-empty string when provided")
    value = raw.strip()
    if not _TERMINAL_ID_PATTERN.match(value):
        raise ToolValidationError("Field 'terminal_id' must match [a-zA-Z0-9_-]{1,32}")
    return value


# ---------------------------------------------------------------------------
# Handler functions
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
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
    ):
        raise ToolValidationError("Field 'timeout_seconds' must be a positive integer")
    timeout_seconds = min(timeout_seconds, 1800)

    background = payload.get("background", False)
    if not isinstance(background, bool):
        raise ToolValidationError("Field 'background' must be a boolean")
    if not background and _command_requests_background_execution(command_text):
        raise ToolValidationError(
            "Shell-level backgrounding (trailing &, nohup, disown) is not allowed "
            "for foreground commands. Set background=true on the tool call instead — "
            "the result will be delivered via a completion notification."
        )

    cwd_raw = payload.get("cwd")
    if cwd_raw is not None and (not isinstance(cwd_raw, str) or not cwd_raw.strip()):
        raise ToolValidationError("Field 'cwd' must be a non-empty string when provided")

    env_payload = payload.get("env", {})
    if env_payload is None:
        env_payload = {}
    if not isinstance(env_payload, dict):
        raise ToolValidationError("Field 'env' must be an object")

    terminal_id_raw = payload.get("terminal_id")
    if terminal_id_raw is None:
        if background and privilege == "user":
            # Auto-allocate a bg-<token> id when the agent didn't pick one.
            # Background can't share terminal '0' (the user's main shell)
            # because long-running output would clobber it.
            terminal_id = f"bg-{uuid4().hex[:8]}"
        else:
            terminal_id = _DEFAULT_TERMINAL_ID
    else:
        terminal_id = _validate_terminal_id(terminal_id_raw)

    # Hard guardrail: background never gets to share the main shell.
    if background and privilege == "user" and terminal_id == _DEFAULT_TERMINAL_ID:
        raise ToolValidationError(
            "background=true cannot use terminal_id='0' (the user's main shell). "
            "Pick a descriptive named id ('build', 'tests', 'server'...) or omit "
            "terminal_id to auto-allocate (bg-<token>)."
        )

    terminal_auto = terminal_id.startswith("auto-") or terminal_id.startswith("bg-")

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
        background=background,
        terminal_id=terminal_id,
        terminal_auto=terminal_auto,
    )


async def handle_run_user(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    return await _handle_run_with_privilege(payload, runtime=runtime, privilege="user")


async def handle_run_root(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    return await _handle_run_with_privilege(payload, runtime=runtime, privilege="root")


async def handle_terminal_list(
    payload: dict[str, Any],
    runtime: ToolRuntimeContext,
) -> dict[str, Any]:
    """Snapshot the active terminals for this chat session."""
    _ = payload  # no parameters
    session_id = require_runtime_session_id(runtime)
    await _ensure_session_exists(session_id)
    manager = get_terminal_manager()
    return {
        "session_id": str(session_id),
        "terminals": manager.descriptors_for(session_id),
    }


async def handle_terminal_read(
    payload: dict[str, Any],
    runtime: ToolRuntimeContext,
) -> dict[str, Any]:
    """Return ANSI-stripped recent output from a specific terminal."""
    session_id = require_runtime_session_id(runtime)
    terminal_id = _validate_terminal_id(payload.get("terminal_id"))
    tail_bytes_raw = payload.get("tail_bytes", 8000)
    if (
        not isinstance(tail_bytes_raw, int)
        or isinstance(tail_bytes_raw, bool)
        or tail_bytes_raw < 256
    ):
        raise ToolValidationError("Field 'tail_bytes' must be an integer >= 256")

    await _ensure_session_exists(session_id)
    runtime_instance = await get_runtime().ensure(session_id)
    manager = get_terminal_manager()
    try:
        output = await manager.read_pane_tail(
            runtime=runtime_instance,
            session_id=session_id,
            terminal_id=terminal_id,
            tail_bytes=tail_bytes_raw,
        )
    except ValueError as exc:
        raise ToolValidationError(str(exc)) from exc
    return {
        "session_id": str(session_id),
        "terminal_id": terminal_id,
        "output": _truncate_runtime_exec_text(output),
    }


async def handle_terminal_close(
    payload: dict[str, Any],
    runtime: ToolRuntimeContext,
) -> dict[str, Any]:
    """Kill a tmux-backed terminal and remove its pill from the chat UI."""
    session_id = require_runtime_session_id(runtime)
    terminal_id = _validate_terminal_id(payload.get("terminal_id"))
    if terminal_id == _DEFAULT_TERMINAL_ID:
        raise ToolValidationError(
            "Refusing to close terminal '0' — that's the user's primary shared "
            "shell. Only close named or auto-allocated terminals."
        )

    await _ensure_session_exists(session_id)
    manager = get_terminal_manager()
    existed = any(rec.terminal_id == terminal_id for rec in manager.list_terminals(session_id))
    await manager.terminate(session_id, terminal_id=terminal_id)
    return {
        "session_id": str(session_id),
        "terminal_id": terminal_id,
        "closed": existed,
    }
