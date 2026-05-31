"""Native module: runtime — SSH/tmux shell execution."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.services.araios.runtime_services import get_ws_manager, notify_runtime_job_completed
from app.services.runtime.ssh_runtime import get_runtime_terminal_manager, runtime_configured
from app.services.runtime.terminal_manager import TerminalBlockedError, TerminalUnavailableError
from app.services.runtime.tmux import validate_terminal_id
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolRuntimeContext


def _session_key(runtime: ToolRuntimeContext) -> str:
    session_id = runtime.runtime_session_id or runtime.session_id
    if session_id is None:
        raise ToolValidationError("Runtime tool requires an active session context.")
    return str(session_id)


def _string_field(payload: dict[str, Any], key: str, *, required: bool = False) -> str | None:
    value = payload.get(key)
    if value is None:
        if required:
            raise ToolValidationError(f"Field '{key}' is required.")
        return None
    if not isinstance(value, str):
        raise ToolValidationError(f"Field '{key}' must be a string.")
    normalized = value.strip()
    if required and not normalized:
        raise ToolValidationError(f"Field '{key}' must be a non-empty string.")
    return normalized or None


def _timeout(payload: dict[str, Any]) -> int:
    value = payload.get("timeout_seconds", 300)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolValidationError("Field 'timeout_seconds' must be a positive integer.")
    return min(value, 3600)


def _env(payload: dict[str, Any]) -> dict[str, str] | None:
    value = payload.get("env")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ToolValidationError("Field 'env' must be an object.")
    return {str(key): str(item) for key, item in value.items()}


def _terminal_ids(payload: dict[str, Any], *, required: bool) -> list[str] | None:
    values: list[str] = []
    terminal_id = _string_field(payload, "terminal_id")
    if terminal_id is not None:
        values.append(terminal_id)
    raw_ids = payload.get("terminal_ids")
    if raw_ids is not None:
        if not isinstance(raw_ids, list):
            raise ToolValidationError("Field 'terminal_ids' must be an array of strings.")
        for item in raw_ids:
            if not isinstance(item, str) or not item.strip():
                raise ToolValidationError(
                    "Field 'terminal_ids' must contain only non-empty strings."
                )
            values.append(item.strip())
    if not values:
        if required:
            raise ToolValidationError("Provide 'terminal_id' or 'terminal_ids'.")
        return None
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        terminal_id = validate_terminal_id(value)
        if terminal_id in seen:
            continue
        seen.add(terminal_id)
        deduped.append(terminal_id)
    return deduped


def _tail_bytes(payload: dict[str, Any]) -> int:
    value = payload.get("tail_bytes", 2_000)
    if not isinstance(value, int) or isinstance(value, bool) or value < 256:
        raise ToolValidationError("Field 'tail_bytes' must be an integer >= 256.")
    return min(value, 200_000)


def _bool_field(payload: dict[str, Any], key: str, *, default: bool = False) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ToolValidationError(f"Field '{key}' must be a boolean.")
    return value


async def _broadcast(session_id: str, payload: dict[str, Any]) -> None:
    manager = get_ws_manager()
    if manager is None:
        return
    await manager.broadcast(session_id, {"session_id": session_id, **payload})


async def handle_terminal_list(
    payload: dict[str, Any], runtime: ToolRuntimeContext
) -> dict[str, Any]:
    if not await runtime_configured(
        instance_name=runtime.instance_name, session_factory=runtime.db_session_factory
    ):
        raise ToolValidationError("Runtime SSH target is not configured.")

    session_id = _session_key(runtime)
    terminal_ids = _terminal_ids(payload, required=False)
    terminal_manager = await get_runtime_terminal_manager(
        instance_name=runtime.instance_name, session_factory=runtime.db_session_factory
    )
    terminals = await terminal_manager.list_terminals(session_id, terminal_ids=terminal_ids)
    items = [terminal.to_dict() for terminal in terminals]
    return {
        "session_id": session_id,
        "ok": True,
        "items": items,
    }


async def handle_terminal_read(
    payload: dict[str, Any], runtime: ToolRuntimeContext
) -> dict[str, Any]:
    if not await runtime_configured(
        instance_name=runtime.instance_name, session_factory=runtime.db_session_factory
    ):
        raise ToolValidationError("Runtime SSH target is not configured.")

    session_id = _session_key(runtime)
    terminal_ids = _terminal_ids(payload, required=True)
    assert terminal_ids is not None
    terminal_manager = await get_runtime_terminal_manager(
        instance_name=runtime.instance_name, session_factory=runtime.db_session_factory
    )
    results = await terminal_manager.read_tails(
        session_id,
        terminal_ids=terminal_ids,
        tail_bytes=_tail_bytes(payload),
    )
    return {
        "session_id": session_id,
        "ok": all(bool(item.get("ok")) for item in results),
        "items": results,
    }


async def handle_terminal_close(
    payload: dict[str, Any], runtime: ToolRuntimeContext
) -> dict[str, Any]:
    if not await runtime_configured(
        instance_name=runtime.instance_name, session_factory=runtime.db_session_factory
    ):
        raise ToolValidationError("Runtime SSH target is not configured.")

    session_id = _session_key(runtime)
    terminal_ids = _terminal_ids(payload, required=True)
    assert terminal_ids is not None
    terminal_manager = await get_runtime_terminal_manager(
        instance_name=runtime.instance_name, session_factory=runtime.db_session_factory
    )
    results = await terminal_manager.close_terminals(session_id, terminal_ids=terminal_ids)
    for result in results:
        if result.get("ok"):
            await _broadcast(
                session_id,
                {
                    "type": "terminal_closed",
                    "terminal_id": result["terminal_id"],
                },
            )
    return {
        "session_id": session_id,
        "ok": all(bool(item.get("ok")) for item in results),
        "items": results,
    }


async def handle_user(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    if not await runtime_configured(
        instance_name=runtime.instance_name, session_factory=runtime.db_session_factory
    ):
        raise ToolValidationError("Runtime SSH target is not configured.")

    session_id = _session_key(runtime)
    shell_command = _string_field(payload, "shell_command", required=True)
    assert shell_command is not None
    terminal_id = validate_terminal_id(_string_field(payload, "terminal_id") or "0")
    cwd = _string_field(payload, "cwd")
    timeout_seconds = _timeout(payload)
    background = _bool_field(payload, "background")
    env = _env(payload)

    terminal_manager = await get_runtime_terminal_manager(
        instance_name=runtime.instance_name, session_factory=runtime.db_session_factory
    )
    if background:
        job_terminal_id = _string_field(payload, "terminal_id") or f"bg-{uuid4().hex[:8]}"
        if job_terminal_id == "0":
            raise ToolValidationError("Background runtime commands cannot use terminal_id '0'.")
        job_terminal_id = validate_terminal_id(job_terminal_id)

        async def _mark_idle() -> None:
            await _broadcast(
                session_id,
                {
                    "type": "terminal_busy",
                    "terminal_id": job_terminal_id,
                    "busy": False,
                    "last_command": shell_command,
                    "last_cwd": cwd,
                },
            )

        async def _on_complete(job: dict[str, object], stdout_tail: str, stderr_tail: str) -> None:
            await notify_runtime_job_completed(
                session_id,
                job,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )

        try:
            handle = await terminal_manager.start_background_command(
                session_id,
                shell_command,
                terminal_id=job_terminal_id,
                cwd=cwd,
                env=env,
                on_complete=_on_complete,
                on_terminal_idle=_mark_idle,
                defer_watch=True,
            )
        except TerminalBlockedError as exc:
            raise ToolValidationError(
                f"Terminal '{job_terminal_id}' is busy with foreground process: "
                f"{exc.current_command or 'unknown'}."
            ) from exc
        except TerminalUnavailableError as exc:
            detail = f": {exc.detail}" if exc.detail else ""
            raise ToolValidationError(
                f"Runtime terminal unavailable ({exc.reason}){detail}"
            ) from exc

        await _broadcast(
            session_id,
            {
                "type": "terminal_opened",
                "terminal_id": job_terminal_id,
                "label": job_terminal_id,
                "created_by": "agent",
                "auto": False,
            },
        )
        await _broadcast(
            session_id,
            {
                "type": "terminal_busy",
                "terminal_id": job_terminal_id,
                "busy": True,
                "last_command": shell_command,
                "last_cwd": cwd,
            },
        )
        terminal_manager.watch_background_command(
            handle,
            on_complete=_on_complete,
            on_terminal_idle=_mark_idle,
        )
        return {
            "session_id": session_id,
            "terminal_id": job_terminal_id,
            "job_id": handle.id,
            "status": "running",
            "stdout": "",
            "stderr": "",
            "metadata": {
                "terminal_id": job_terminal_id,
                "cwd": cwd,
                "command": shell_command,
                "background": True,
            },
        }

    await _broadcast(
        session_id,
        {
            "type": "terminal_opened",
            "terminal_id": terminal_id,
            "label": "main" if terminal_id == "0" else terminal_id,
            "created_by": "agent",
            "auto": terminal_id == "0",
        },
    )
    await _broadcast(
        session_id,
        {
            "type": "terminal_busy",
            "terminal_id": terminal_id,
            "busy": True,
            "last_command": shell_command,
            "last_cwd": cwd,
        },
    )
    try:
        result = await terminal_manager.run_command(
            session_id,
            shell_command,
            terminal_id=terminal_id,
            timeout=timeout_seconds,
            cwd=cwd,
            env=env,
        )
    except TerminalBlockedError as exc:
        raise ToolValidationError(
            f"Terminal '{terminal_id}' is busy with foreground process: {exc.current_command or 'unknown'}."
        ) from exc
    except TerminalUnavailableError as exc:
        detail = f": {exc.detail}" if exc.detail else ""
        raise ToolValidationError(f"Runtime terminal unavailable ({exc.reason}){detail}") from exc
    finally:
        await _broadcast(
            session_id,
            {
                "type": "terminal_busy",
                "terminal_id": terminal_id,
                "busy": False,
                "last_command": shell_command,
                "last_cwd": cwd,
            },
        )

    return {
        "session_id": session_id,
        "terminal_id": terminal_id,
        "exit_status": result.exit_status,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "metadata": {
            "terminal_id": terminal_id,
            "cwd": cwd,
            "command": shell_command,
        },
    }
