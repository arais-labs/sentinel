import json
import shlex
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.services.runtime.desktop import RuntimeDesktopManager, RuntimeDesktopError
from app.services.runtime.workspace import WorkspaceLocation


def desktop_manager(tools=(), state="running", desktop="xfce"):
    transport = SimpleNamespace(
        run=AsyncMock(),
        is_ready=AsyncMock(return_value=state == "running"),
        workspace_status=AsyncMock(return_value={"state": state}),
        desktop_socket=AsyncMock(return_value=("/tmp/test-desktop.sock", None)),
    )
    manager = RuntimeDesktopManager(
        SimpleNamespace(ssh=transport),
        workspace_location=WorkspaceLocation(
            "/project",
            "/var/lib/sentinel",
            str(uuid4()),
            "/project",
            tools,
            desktop=desktop,
        ),
    )
    return manager, transport


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_remote", [False, True])
async def test_session_cleanup_closes_tunnel_but_preserves_shared_desktop(stop_remote):
    manager, transport = desktop_manager()
    listener = SimpleNamespace(close=Mock(), wait_closed=AsyncMock())
    manager._handles[str(uuid4())] = SimpleNamespace(listener=listener)
    await manager.close_all(stop_remote=stop_remote)
    listener.close.assert_called_once()
    listener.wait_closed.assert_awaited_once()
    assert manager._handles == {}
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_unselected_desktop_never_runs_commands_or_starts_workspace():
    manager, transport = desktop_manager(desktop="none")
    assert (await manager.status())["state"] == "not_installed"
    with pytest.raises(RuntimeDesktopError, match="Add the Desktop"):
        await manager.ensure_session_desktop(uuid4())
    transport.is_ready.assert_not_awaited()
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_and_stop_never_resurrect_a_stopped_workspace():
    manager, transport = desktop_manager(state="stopped")
    assert (await manager.status())["state"] == "workspace_stopped"
    await manager.stop()
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_desktop_stop_failure_still_closes_local_tunnels(monkeypatch):
    from app.services.runtime import workspace_containers

    manager, transport = desktop_manager()
    transport.run.return_value = (
        0,
        json.dumps({"ok": True, "state": "stopped"}),
        "",
    )
    manager._command = AsyncMock(
        side_effect=workspace_containers.WorkspaceContainerError("disconnected")
    )
    listener = SimpleNamespace(close=Mock(), wait_closed=AsyncMock())
    manager._handles[str(uuid4())] = SimpleNamespace(listener=listener)
    monkeypatch.setattr(
        workspace_containers,
        "request",
        AsyncMock(side_effect=workspace_containers.WorkspaceContainerError("disconnected")),
    )
    with pytest.raises(RuntimeDesktopError, match="could not stop"):
        await manager.stop()
    listener.close.assert_called_once()
    assert manager._handles == {}


@pytest.mark.asyncio
async def test_browser_reuses_desktop_geometry_and_read_only_reconnect_does_not_start_it(
    monkeypatch,
):
    graphics = AsyncMock(return_value={})
    monkeypatch.setattr("app.services.runtime.desktop.workspace_containers.request", graphics)
    manager, transport = desktop_manager()
    transport.run.return_value = SimpleNamespace(
        exit_status=0,
        stderr="",
        stdout=json.dumps(
            {
                "ok": True,
                "state": "running",
                "geometry": "1280x800",
                "display": ":1",
                "port": 5901,
            }
        ),
    )
    first = await manager.ensure_session_desktop("one")
    second = await manager.get_session_desktop("two")
    assert first.display == second.display == ":1"
    assert first.geometry == second.geometry == "1280x800"
    for call in transport.run.await_args_list:
        assert json.loads(shlex.split(call.args[0])[3])["action"] == "status"
    await manager.close_all()
    assert [call.args[0] for call in graphics.await_args_list] == [
        "display_start",
        "display_start",
    ]


@pytest.mark.asyncio
async def test_metal_connection_failure_never_starts_a_software_desktop(monkeypatch):
    from app.services.runtime.workspace_containers import WorkspaceContainerError

    monkeypatch.setattr(
        "app.services.runtime.desktop.workspace_containers.request",
        AsyncMock(side_effect=WorkspaceContainerError("renderer missing")),
    )
    manager, transport = desktop_manager()
    with pytest.raises(RuntimeDesktopError, match="Metal desktop graphics unavailable"):
        await manager.ensure_session_desktop(uuid4())
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_view_get_reports_stopped_without_starting(monkeypatch):
    from app.services.runtime import control

    sid = str(uuid4())
    manager = SimpleNamespace(
        enabled=True,
        status=AsyncMock(return_value={"state": "stopped"}),
        ensure_session_desktop=AsyncMock(),
        get_session_desktop=AsyncMock(),
    )
    monkeypatch.setattr(control, "runtime_configured", AsyncMock(return_value=True))
    monkeypatch.setattr(control, "get_runtime_desktop_manager", AsyncMock(return_value=manager))
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: sid)),
        close=AsyncMock(),
    )
    request = SimpleNamespace(state=SimpleNamespace(instance_name="test"), path_params={})
    result = await control.live_view_response(
        request=request,
        session_id=sid,
        db=db,
        geometry=None,
        resolution_presets={"1920x1200"},
    )
    assert result.state == "stopped"
    assert not result.available
    manager.ensure_session_desktop.assert_not_awaited()
    manager.get_session_desktop.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [False, True])
async def test_direct_connection_authorizes_session_before_resolving_socket(monkeypatch, exists):
    from fastapi import HTTPException
    from app.routers.runtime import runtime_desktop_connection
    from app.services.runtime import control, ssh_runtime

    sid = uuid4()
    authorize = AsyncMock(return_value=exists)
    monkeypatch.setattr(control, "_runtime_session_exists", authorize)
    manager = SimpleNamespace(
        get_session_desktop=AsyncMock(
            return_value=SimpleNamespace(socket_path="/tmp/workspace.sock")
        )
    )

    async def resolve(**kwargs):
        authorize.assert_awaited_once()
        assert kwargs == {"session_id": sid, "instance_name": "test"}
        return manager

    get_manager = AsyncMock(side_effect=resolve)
    monkeypatch.setattr(ssh_runtime, "get_runtime_desktop_manager", get_manager)
    request = SimpleNamespace(state=SimpleNamespace(instance_name="test"), path_params={})
    if exists:
        assert await runtime_desktop_connection(request, sid, None) == {
            "socket": "/tmp/workspace.sock"
        }
    else:
        with pytest.raises(HTTPException) as error:
            await runtime_desktop_connection(request, sid, None)
        assert error.value.status_code == 404
        get_manager.assert_not_awaited()
