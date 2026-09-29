from unittest.mock import AsyncMock

import pytest

from app.routers import ws
from app.services.runtime.panes import TmuxPanes


@pytest.mark.asyncio
async def test_cleanup_transport_never_starts_and_preserves_live_errors(monkeypatch):
    from uuid import uuid4
    from app.services.runtime.container_transport import ContainerTransport
    from app.services.runtime import workspace_containers as containers

    transport = ContainerTransport(uuid4(), "/project", [])
    readiness = AsyncMock(side_effect=AssertionError("Cleanup must not ensure readiness"))
    monkeypatch.setattr(transport, "wait_ready", readiness)
    status = AsyncMock(return_value={"state": "stopped"})
    monkeypatch.setattr(transport, "workspace_status", status)
    execute = AsyncMock(side_effect=containers.WorkspaceContainerError("exec failed"))
    monkeypatch.setattr(containers, "request", execute)
    assert await transport.run_if_running("true") is None
    execute.assert_not_awaited()
    status.side_effect = [{"state": "running"}, {"state": "stopping"}]
    assert await transport.run_if_running("true") is None
    status.side_effect = None
    status.return_value = {"state": "running"}
    with pytest.raises(containers.WorkspaceContainerError, match="exec failed"):
        await transport.run_if_running("true")
    readiness.assert_not_awaited()


@pytest.mark.asyncio
async def test_unattached_chat_has_no_panes_and_does_not_start_runtime(monkeypatch):
    monkeypatch.setattr(ws, "runtime_configured", AsyncMock(return_value=False))
    manager = AsyncMock()
    monkeypatch.setattr(ws, "get_runtime_terminal_manager", manager)

    assert await ws._initial_panes("instance", "chat") == []
    manager.assert_not_awaited()


@pytest.mark.asyncio
async def test_attached_chat_receives_existing_panes_across_windows(monkeypatch):
    monkeypatch.setattr(ws, "runtime_configured", AsyncMock(return_value=True))
    monkeypatch.setattr(ws, "get_runtime_terminal_manager", AsyncMock())
    panes = [
        {"pane_id": "%0", "window_id": "@0", "busy": False},
        {"pane_id": "%1", "window_id": "@0", "busy": True},
        {"pane_id": "%2", "window_id": "@1", "busy": False},
    ]
    tree = AsyncMock(return_value=[{"panes": panes[:2]}, {"panes": panes[2:]}])
    monkeypatch.setattr(TmuxPanes, "tree", tree)

    assert await ws._initial_panes("instance", "chat") == panes
    tree.assert_awaited_once_with("chat")


@pytest.mark.asyncio
async def test_preparing_workspace_pane_listing_does_not_wait_or_start_runtime(
    monkeypatch,
):
    import asyncio
    from types import SimpleNamespace
    from uuid import uuid4
    from app.services.runtime.container_transport import ContainerTransport
    from app.services.runtime import workspace_containers as containers

    workspace_id = uuid4()
    transport = ContainerTransport(workspace_id, "/project", [])
    monkeypatch.setattr(
        containers,
        "statuses",
        AsyncMock(return_value={str(workspace_id): {"state": "preparing"}}),
    )
    start = AsyncMock()
    monkeypatch.setattr(containers, "start", start)
    transport.run = AsyncMock(
        side_effect=AssertionError("Listing must not execute while preparing")
    )
    manager = SimpleNamespace(ssh=transport)
    trees = await asyncio.wait_for(
        asyncio.gather(*(TmuxPanes(manager).tree("chat") for _ in range(30))), 1
    )
    assert trees == [[]] * 30
    start.assert_not_awaited()
    transport.run.assert_not_awaited()


def test_chat_connects_before_slow_terminal_discovery(monkeypatch):
    import asyncio
    import threading
    from fastapi.testclient import TestClient
    from app.main import app
    from tests.fake_db import FakeDB
    from tests.helpers import install_fake_db_overrides, restore_test_app

    finished = threading.Event()
    panes = [{"pane_id": "%0", "window_id": "@0", "busy": False}]

    async def delayed_panes(*args):
        await asyncio.sleep(0.2)
        finished.set()
        return panes

    monkeypatch.setattr(ws, "_initial_panes", delayed_panes)
    old = install_fake_db_overrides(app_db=FakeDB())
    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        sid = client.post(
            "/api/v1/instances/main/sessions", json={"title": "fast reconnect"}
        ).json()["id"]
        with client.websocket_connect(f"/ws/instances/main/sessions/{sid}/stream") as socket:
            assert socket.receive_json()["type"] == "connected"
            assert not finished.is_set(), "Remote terminal discovery must not block chat readiness"
            event = socket.receive_json()
            assert event == {"type": "panes_changed", "panes": panes}
    finally:
        restore_test_app(old)
