from __future__ import annotations

from uuid import uuid4

import pytest

from app.schemas.runtime import RuntimeExecResult
from app.services.runtime.terminal_manager import BackgroundJobHandle
from app.services.tools.registry_builder import build_default_registry
from app.services.tools.executor import ToolExecutor, ToolValidationError
from app.services.tools.registry import ToolRuntimeContext


class _TerminalManagerStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "session_id": session_id,
                "command": command,
                "terminal_id": terminal_id,
                "timeout": timeout,
                "cwd": cwd,
                "env": env,
            }
        )
        return RuntimeExecResult(exit_status=0, stdout="ok\n", stderr="")

    async def start_background_command(
        self,
        session_id: str,
        command: str,
        *,
        terminal_id: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        on_complete=None,
        on_terminal_idle=None,
        defer_watch: bool = False,
    ) -> BackgroundJobHandle:
        resolved_terminal_id = terminal_id or "bg-test"
        self.calls.append(
            {
                "session_id": session_id,
                "command": command,
                "terminal_id": resolved_terminal_id,
                "timeout": None,
                "cwd": cwd,
                "env": env,
                "background": True,
            }
        )
        return BackgroundJobHandle(
            id="job-123",
            session_id=session_id,
            terminal_id=resolved_terminal_id,
            command=command,
            status="running",
            run_path="/state/runtime/jobs/job-123/run.sh",
            result_path="/srv/sentinel/session/state/runtime/jobs/job-123/done.json",
            log_offset=0,
        )

    def watch_background_command(
        self,
        handle: BackgroundJobHandle,
        *,
        on_complete=None,
        on_terminal_idle=None,
    ) -> None:
        self.calls.append(
            {
                "session_id": handle.session_id,
                "command": handle.command,
                "terminal_id": handle.terminal_id,
                "watch": True,
            }
        )

    async def list_terminals(self, session_id: str, *, terminal_ids: list[str] | None = None):
        self.calls.append(
            {"session_id": session_id, "terminal_ids": terminal_ids, "action": "list"}
        )

        class _Descriptor:
            def __init__(self, terminal_id: str) -> None:
                self.terminal_id = terminal_id

            def to_dict(self) -> dict[str, object]:
                return {
                    "terminal_id": self.terminal_id,
                    "label": "main" if self.terminal_id == "0" else self.terminal_id,
                    "status": "running",
                    "busy": False,
                    "last_command": None,
                    "last_cwd": None,
                    "auto": self.terminal_id.startswith("bg-"),
                    "created_by": "runtime",
                }

        return [_Descriptor(item) for item in (terminal_ids or ["0", "bg-test"])]

    async def read_tails(
        self, session_id: str, *, terminal_ids: list[str], tail_bytes: int = 8_000
    ):
        self.calls.append(
            {
                "session_id": session_id,
                "terminal_ids": terminal_ids,
                "tail_bytes": tail_bytes,
                "action": "read",
            }
        )
        return [
            {"terminal_id": item, "ok": True, "output": f"tail:{item}"} for item in terminal_ids
        ]

    async def close_terminals(self, session_id: str, *, terminal_ids: list[str]):
        self.calls.append(
            {"session_id": session_id, "terminal_ids": terminal_ids, "action": "close"}
        )
        return [
            {"terminal_id": item, "ok": True, "closed": True, "status": "stopped"}
            for item in terminal_ids
        ]


async def _runtime_configured_stub(**_kwargs) -> bool:
    return True


async def _terminal_manager_stub(manager: _TerminalManagerStub, **_kwargs) -> _TerminalManagerStub:
    return manager


@pytest.mark.asyncio
async def test_runtime_tool_is_registered_with_legacy_shape() -> None:
    registry = build_default_registry()
    tool = registry.get("runtime")

    assert tool is not None
    assert tool.parameters_schema["properties"]["command"]["enum"] == [
        "terminal_close",
        "terminal_list",
        "terminal_read",
        "user",
    ]
    assert "command" in tool.parameters_schema["required"]
    assert "shell_command" not in tool.parameters_schema["required"]


@pytest.mark.asyncio
async def test_runtime_tool_runs_command_through_terminal_manager(monkeypatch) -> None:
    from app.services.araios.system_modules.runtime import handlers

    stub = _TerminalManagerStub()
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(
        handlers,
        "get_runtime_terminal_manager",
        lambda **kwargs: _terminal_manager_stub(stub, **kwargs),
    )
    monkeypatch.setattr(handlers, "get_ws_manager", lambda: None)

    registry = build_default_registry()
    executor = ToolExecutor(registry)
    session_id = uuid4()

    result, _duration_ms = await executor.execute(
        "runtime",
        {
            "command": "user",
            "shell_command": "echo ok",
            "cwd": "/workspace",
            "terminal_id": "0",
            "timeout_seconds": 5,
        },
        runtime=ToolRuntimeContext(session_id=session_id),
    )

    assert result["exit_status"] == 0
    assert result["stdout"] == "ok\n"
    assert result["terminal_id"] == "0"
    assert stub.calls == [
        {
            "session_id": str(session_id),
            "command": "echo ok",
            "terminal_id": "0",
            "timeout": 5,
            "cwd": "/workspace",
            "env": None,
        }
    ]


@pytest.mark.asyncio
async def test_runtime_tool_starts_background_command(monkeypatch) -> None:
    from app.services.araios.system_modules.runtime import handlers

    stub = _TerminalManagerStub()
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(
        handlers,
        "get_runtime_terminal_manager",
        lambda **kwargs: _terminal_manager_stub(stub, **kwargs),
    )
    monkeypatch.setattr(handlers, "get_ws_manager", lambda: None)

    registry = build_default_registry()
    executor = ToolExecutor(registry)
    session_id = uuid4()

    result, _duration_ms = await executor.execute(
        "runtime",
        {
            "command": "user",
            "shell_command": "sleep 1 && echo done",
            "cwd": "/workspace",
            "background": True,
        },
        runtime=ToolRuntimeContext(session_id=session_id),
    )

    assert result["status"] == "running"
    assert result["job_id"] == "job-123"
    assert result["terminal_id"].startswith("bg-")
    assert stub.calls == [
        {
            "session_id": str(session_id),
            "command": "sleep 1 && echo done",
            "terminal_id": result["terminal_id"],
            "timeout": None,
            "cwd": "/workspace",
            "env": None,
            "background": True,
        },
        {
            "session_id": str(session_id),
            "command": "sleep 1 && echo done",
            "terminal_id": result["terminal_id"],
            "watch": True,
        },
    ]


@pytest.mark.asyncio
async def test_runtime_tool_allows_shell_control_operators(monkeypatch) -> None:
    from app.services.araios.system_modules.runtime import handlers

    stub = _TerminalManagerStub()
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(
        handlers,
        "get_runtime_terminal_manager",
        lambda **kwargs: _terminal_manager_stub(stub, **kwargs),
    )
    monkeypatch.setattr(handlers, "get_ws_manager", lambda: None)

    registry = build_default_registry()
    executor = ToolExecutor(registry)
    session_id = uuid4()

    result, _duration_ms = await executor.execute(
        "runtime",
        {
            "command": "user",
            "shell_command": "printf 'random local change test\\n' > RANDOM_LOCAL_CHANGE.txt && git status --short",
            "cwd": "/workspace/clickhouse-analytics",
            "terminal_id": "0",
        },
        runtime=ToolRuntimeContext(session_id=session_id),
    )

    assert result["exit_status"] == 0
    assert stub.calls == [
        {
            "session_id": str(session_id),
            "command": "printf 'random local change test\\n' > RANDOM_LOCAL_CHANGE.txt && git status --short",
            "terminal_id": "0",
            "timeout": 300,
            "cwd": "/workspace/clickhouse-analytics",
            "env": None,
        }
    ]


@pytest.mark.asyncio
async def test_runtime_tool_lists_selected_terminals(monkeypatch) -> None:
    from app.services.araios.system_modules.runtime import handlers

    stub = _TerminalManagerStub()
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(
        handlers,
        "get_runtime_terminal_manager",
        lambda **kwargs: _terminal_manager_stub(stub, **kwargs),
    )

    registry = build_default_registry()
    executor = ToolExecutor(registry)
    session_id = uuid4()

    result, _duration_ms = await executor.execute(
        "runtime",
        {
            "command": "terminal_list",
            "terminal_ids": ["0", "bg-test"],
        },
        runtime=ToolRuntimeContext(session_id=session_id),
    )

    assert result["ok"] is True
    assert [item["terminal_id"] for item in result["items"]] == ["0", "bg-test"]
    assert stub.calls == [
        {"session_id": str(session_id), "terminal_ids": ["0", "bg-test"], "action": "list"}
    ]


@pytest.mark.asyncio
async def test_runtime_tool_reads_multiple_terminals(monkeypatch) -> None:
    from app.services.araios.system_modules.runtime import handlers

    stub = _TerminalManagerStub()
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(
        handlers,
        "get_runtime_terminal_manager",
        lambda **kwargs: _terminal_manager_stub(stub, **kwargs),
    )

    registry = build_default_registry()
    executor = ToolExecutor(registry)
    session_id = uuid4()

    result, _duration_ms = await executor.execute(
        "runtime",
        {
            "command": "terminal_read",
            "terminal_ids": ["0", "bg-test"],
            "tail_bytes": 1000,
        },
        runtime=ToolRuntimeContext(session_id=session_id),
    )

    assert result["ok"] is True
    assert "stdout" not in result
    assert "terminals" not in result
    assert [item["output"] for item in result["items"]] == ["tail:0", "tail:bg-test"]
    assert stub.calls == [
        {
            "session_id": str(session_id),
            "terminal_ids": ["0", "bg-test"],
            "tail_bytes": 1000,
            "action": "read",
        }
    ]


@pytest.mark.asyncio
async def test_runtime_tool_closes_multiple_terminals_including_main(monkeypatch) -> None:
    from app.services.araios.system_modules.runtime import handlers

    stub = _TerminalManagerStub()
    monkeypatch.setattr(handlers, "runtime_configured", _runtime_configured_stub)
    monkeypatch.setattr(
        handlers,
        "get_runtime_terminal_manager",
        lambda **kwargs: _terminal_manager_stub(stub, **kwargs),
    )
    monkeypatch.setattr(handlers, "get_ws_manager", lambda: None)

    registry = build_default_registry()
    executor = ToolExecutor(registry)
    session_id = uuid4()

    result, _duration_ms = await executor.execute(
        "runtime",
        {
            "command": "terminal_close",
            "terminal_ids": ["0", "bg-test"],
        },
        runtime=ToolRuntimeContext(session_id=session_id),
    )

    assert result["ok"] is True
    assert [item["terminal_id"] for item in result["items"]] == ["0", "bg-test"]
    assert all(item["closed"] for item in result["items"])
    assert stub.calls == [
        {"session_id": str(session_id), "terminal_ids": ["0", "bg-test"], "action": "close"}
    ]
