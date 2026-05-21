from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.runtime.port_forwards import RuntimePortForwardManager
from app.services.tools.executor import ToolExecutor, ToolValidationError
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.registry_builder import build_default_registry


class _ListenerStub:
    def __init__(self) -> None:
        self.closed = False
        self.waited = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.waited = True


class _SSHStub:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.listeners: list[_ListenerStub] = []

    async def forward_local_port(
        self,
        listen_host: str,
        listen_port: int,
        target_host: str,
        target_port: int,
    ) -> _ListenerStub:
        listener = _ListenerStub()
        self.listeners.append(listener)
        self.calls.append(
            {
                "listen_host": listen_host,
                "listen_port": listen_port,
                "target_host": target_host,
                "target_port": target_port,
            }
        )
        return listener


@pytest.mark.asyncio
async def test_port_forward_manager_reuses_same_session_target() -> None:
    ssh = _SSHStub()
    manager = RuntimePortForwardManager(ssh)  # type: ignore[arg-type]

    first = await manager.open_forward(
        session_id="session-1",
        target_host="localhost",
        target_port=5173,
        protocol="http",
    )
    second = await manager.open_forward(
        session_id="session-1",
        target_host="127.0.0.1",
        target_port=5173,
        protocol="http",
    )

    assert first.forward_id == second.forward_id
    assert first.target_host == "127.0.0.1"
    assert first.proxy_path == f"/sessions/session-1/runtime/forwards/{first.forward_id}/"
    assert len(ssh.calls) == 1


@pytest.mark.asyncio
async def test_port_forward_manager_closes_session_forwards() -> None:
    ssh = _SSHStub()
    manager = RuntimePortForwardManager(ssh)  # type: ignore[arg-type]

    await manager.open_forward(
        session_id="session-1",
        target_host="127.0.0.1",
        target_port=3000,
    )
    await manager.open_forward(
        session_id="session-2",
        target_host="127.0.0.1",
        target_port=3000,
    )

    await manager.close_session("session-1")

    assert ssh.listeners[0].closed is True
    assert ssh.listeners[0].waited is True
    assert ssh.listeners[1].closed is False
    assert len(await manager.list_forwards(session_id="session-1")) == 0
    assert len(await manager.list_forwards(session_id="session-2")) == 1


@pytest.mark.asyncio
async def test_port_forward_tool_open_list_close(monkeypatch) -> None:
    from app.services.araios.system_modules.port_forward import handlers

    manager = RuntimePortForwardManager(_SSHStub())  # type: ignore[arg-type]
    monkeypatch.setattr(handlers, "runtime_configured", lambda: True)
    monkeypatch.setattr(handlers, "get_runtime_port_forward_manager", lambda: manager)

    executor = ToolExecutor(build_default_registry())
    session_id = uuid4()

    opened, _duration_ms = await executor.execute(
        "port_forward",
        {
            "command": "open",
            "port": 4173,
            "host": "localhost",
            "protocol": "ws",
            "label": "preview",
        },
        runtime=ToolRuntimeContext(session_id=session_id),
    )
    assert opened["status"] == "open"
    assert opened["target_host"] == "127.0.0.1"
    assert opened["target_port"] == 4173
    assert opened["protocol"] == "websocket"
    assert opened["proxy_path"].endswith(f"/runtime/forwards/{opened['forward_id']}/")
    assert opened["url"].startswith("/api/v1/instances/main/sessions/")

    listed, _duration_ms = await executor.execute(
        "port_forward",
        {"command": "list"},
        runtime=ToolRuntimeContext(session_id=session_id),
    )
    assert [item["forward_id"] for item in listed["forwards"]] == [opened["forward_id"]]

    closed, _duration_ms = await executor.execute(
        "port_forward",
        {"command": "close", "forward_id": opened["forward_id"]},
        runtime=ToolRuntimeContext(session_id=session_id),
    )
    assert closed["status"] == "closed"


@pytest.mark.asyncio
async def test_port_forward_tool_rejects_non_loopback_target(monkeypatch) -> None:
    from app.services.araios.system_modules.port_forward import handlers

    manager = RuntimePortForwardManager(_SSHStub())  # type: ignore[arg-type]
    monkeypatch.setattr(handlers, "runtime_configured", lambda: True)
    monkeypatch.setattr(handlers, "get_runtime_port_forward_manager", lambda: manager)

    executor = ToolExecutor(build_default_registry())

    with pytest.raises(ToolValidationError, match="Only loopback"):
        await executor.execute(
            "port_forward",
            {
                "command": "open",
                "port": 3000,
                "host": "10.0.0.5",
            },
            runtime=ToolRuntimeContext(session_id=uuid4()),
        )
