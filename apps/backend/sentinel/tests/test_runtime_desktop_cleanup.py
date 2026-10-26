from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.workspaces.desktop.manager import DesktopManager, DesktopError
from app.services.workspaces.paths import WorkspaceLocation


@pytest.fixture(autouse=True)
def no_host_keyboard(monkeypatch):
    # The Mac running the tests has layouts of its own: tests set the ones they need.
    monkeypatch.setattr(
        "app.services.workspaces.desktop.manager.host_keyboard", AsyncMock(return_value=None)
    )


def desktop_manager(tools=(), state="running", desktop="xfce"):
    transport = SimpleNamespace(
        run=AsyncMock(),
        is_ready=AsyncMock(return_value=state == "running"),
        workspace_status=AsyncMock(return_value={"state": state}),
    )
    manager = DesktopManager(
        SimpleNamespace(ssh=transport),
        workspace_location=WorkspaceLocation(
            "/project",
            "/var/lib/sentinel",
            str(uuid4()),
            "/project",
            tools,
            desktop=desktop,
            features=frozenset({"shell"}),
        ),
    )
    return manager, transport


@pytest.mark.asyncio
async def test_unselected_desktop_never_runs_commands_or_starts_workspace():
    manager, transport = desktop_manager(desktop="none")
    assert (await manager.status())["state"] == "not_installed"
    with pytest.raises(DesktopError, match="Choose a desktop"):
        await manager.ensure_session_desktop(uuid4())
    transport.is_ready.assert_not_awaited()
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_never_resurrects_a_stopped_workspace():
    manager, transport = desktop_manager(state="stopped")
    assert (await manager.status())["state"] == "workspace_stopped"
    with pytest.raises(DesktopError, match="Start the workspace"):
        await manager.ensure_session_desktop(uuid4())
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_desktop_runs_with_its_workspace_and_reconnects_never_start_anything(monkeypatch):
    request = AsyncMock()
    monkeypatch.setattr("app.services.workspaces.desktop.manager.lifecycle.request", request)
    monkeypatch.setattr(
        "app.services.workspaces.desktop.manager.host_keyboard", AsyncMock(return_value=None)
    )
    manager, transport = desktop_manager()
    first = await manager.ensure_session_desktop("one")
    second = await manager.get_session_desktop("two")
    assert first.display == second.display == ":0"
    # No guest commands: sentineld provisions the desktop, its default look included.
    transport.run.assert_not_awaited()
    request.assert_not_awaited()


@pytest.mark.asyncio
async def test_host_keyboard_layouts_are_sent_when_they_change(monkeypatch):
    layouts = {"layout": "fr,us", "variant": "mac,mac", "model": "pc105", "options": ""}
    host = AsyncMock(return_value=layouts)
    monkeypatch.setattr("app.services.workspaces.desktop.manager.host_keyboard", host)
    request = AsyncMock(return_value={"applied": {"layout": "fr,us", "variant": "mac,mac"}})
    monkeypatch.setattr("app.services.workspaces.desktop.manager.lifecycle.request", request)
    manager, transport = desktop_manager(desktop="lxqt")
    transport.run.return_value = SimpleNamespace(exit_status=0, stdout="", stderr="")

    await manager.ensure_session_desktop("one")
    await manager.ensure_session_desktop("two")
    assert request.await_count == 1  # unchanged layouts are not sent again
    assert request.await_args.args == ("keyboard",)
    assert request.await_args.kwargs == {
        "workspace": str(manager._workspace_location.workspace_id),
        **layouts,
    }
    host.return_value = {**layouts, "layout": "us", "variant": "mac"}
    await manager.ensure_session_desktop("three")
    assert request.await_count == 2


@pytest.mark.asyncio
async def test_viewer_returns_only_the_webtransport_ticket(monkeypatch):
    request = AsyncMock(
        return_value={"port": 4433, "cert_sha256": "ab" * 32, "ticket": "t" * 32, "expires": 1}
    )
    monkeypatch.setattr("app.services.workspaces.desktop.manager.lifecycle.request", request)
    manager, transport = desktop_manager()
    transport.run.return_value = SimpleNamespace(exit_status=0, stdout="", stderr="")
    assert await manager.viewer("one") == {
        "port": 4433,
        "cert_sha256": "ab" * 32,
        "ticket": "t" * 32,
    }
    assert request.await_args.args == ("display",)


@pytest.mark.asyncio
async def test_viewer_failure_is_reported_not_retried(monkeypatch):
    from app.services.workspaces.lifecycle import WorkspaceLifecycleError

    request = AsyncMock(side_effect=WorkspaceLifecycleError("endpoint down"))
    monkeypatch.setattr("app.services.workspaces.desktop.manager.lifecycle.request", request)
    manager, transport = desktop_manager()
    transport.run.return_value = SimpleNamespace(exit_status=0, stdout="", stderr="")
    with pytest.raises(DesktopError, match="Desktop viewer unavailable: endpoint down"):
        await manager.viewer(uuid4())
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_view_get_reports_stopped_without_starting(monkeypatch):
    from app.services.workspaces import control

    sid = str(uuid4())
    manager = SimpleNamespace(
        enabled=True,
        status=AsyncMock(return_value={"state": "stopped"}),
        ensure_session_desktop=AsyncMock(),
        get_session_desktop=AsyncMock(),
    )
    monkeypatch.setattr(control, "workspace_attached", AsyncMock(return_value=True))
    monkeypatch.setattr(control, "get_desktop_manager", AsyncMock(return_value=manager))
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: sid)),
        close=AsyncMock(),
    )
    request = SimpleNamespace(state=SimpleNamespace(instance_name="test"), path_params={})
    result = await control.desktop_response(
        request=request,
        session_id=sid,
        db=db,
    )
    assert result.state == "stopped"
    assert not result.available
    manager.ensure_session_desktop.assert_not_awaited()
    manager.get_session_desktop.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [False, True])
async def test_direct_connection_authorizes_session_before_issuing_a_ticket(monkeypatch, exists):
    from fastapi import HTTPException
    from app.routers.session_workspace import desktop_viewer
    from app.services.workspaces import control
    from app.services.workspaces import managers

    sid = uuid4()
    authorize = AsyncMock(return_value=exists)
    monkeypatch.setattr(control, "_session_exists", authorize)
    ticket = {"port": 4433, "cert_sha256": "ab" * 32, "ticket": "t" * 32}
    manager = SimpleNamespace(viewer=AsyncMock(return_value=ticket))

    async def resolve(**kwargs):
        authorize.assert_awaited_once()
        assert kwargs == {"session_id": str(sid), "instance_name": "test"}
        return manager

    get_manager = AsyncMock(side_effect=resolve)
    monkeypatch.setattr(managers, "get_desktop_manager", get_manager)
    request = SimpleNamespace(state=SimpleNamespace(instance_name="test"), path_params={})
    if exists:
        assert await desktop_viewer(request, sid, None) == ticket
    else:
        with pytest.raises(HTTPException) as error:
            await desktop_viewer(request, sid, None)
        assert error.value.status_code == 404
        get_manager.assert_not_awaited()


@pytest.mark.asyncio
async def test_clipboard_and_wallpaper_are_sentineld_ops(monkeypatch):
    request = AsyncMock(return_value={"text": "café"})
    monkeypatch.setattr("app.services.workspaces.desktop.manager.lifecycle.request", request)
    manager, transport = desktop_manager(desktop="plasma")
    transport.run.return_value = SimpleNamespace(exit_status=0, stdout="", stderr="")
    workspace = str(manager._workspace_location.workspace_id)

    assert await manager.clipboard({"action": "read", "text": None}) == {"ok": True, "text": "café"}
    request.assert_awaited_with("clipboard", workspace=workspace, direction="read")
    assert await manager.clipboard({"action": "write", "text": "x"}) == {"ok": True}
    request.assert_awaited_with("clipboard", workspace=workspace, direction="write", text="x")
    assert await manager.wallpaper("satin") == {"ok": True}
    request.assert_awaited_with("wallpaper", workspace=workspace, design="satin")
    # Nothing runs in the guest for them but the desktop's own probe.
    assert {call.args[0] for call in transport.run.await_args_list} <= {"test -S /tmp/.X11-unix/X0"}
