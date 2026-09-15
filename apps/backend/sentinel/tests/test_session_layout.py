import asyncio

import pytest

from app.services.modules.builtins.session_layout.module import MODULE
from app.services.ws.ws_manager import ConnectionManager


class Socket:
    def __init__(self):
        self.messages = asyncio.Queue()

    async def send_json(self, payload):
        await self.messages.put(payload)


@pytest.mark.asyncio
async def test_layout_requires_one_displayed_client_and_correlates_reply():
    manager = ConnectionManager()
    socket, other = Socket(), Socket()
    await manager.connect("session", socket)
    await manager.connect("other", other)
    with pytest.raises(RuntimeError, match="No visible layout"):
        await manager.request_layout("session", {"action": "inspect"})
    manager.layout_message("session", socket, {"type": "layout_ready", "ready": True})
    task = asyncio.create_task(manager.request_layout("session", {"action": "inspect"}))
    request = await socket.messages.get()
    reply = {
        "type": "layout_result",
        "request_id": request["request_id"],
        "result": {"ok": True, "panes": []},
    }
    manager.layout_message("other", other, reply)
    assert not task.done()
    manager.layout_message("session", socket, reply)
    assert await task == {"ok": True, "panes": []}
    assert not manager._layout_pending
    await manager.connect("session", other)
    manager.layout_message("session", other, {"type": "layout_ready", "ready": True})
    with pytest.raises(RuntimeError, match="multiple Sentinel windows"):
        await manager.request_layout("session", {"action": "apply"})


@pytest.mark.asyncio
async def test_layout_timeout_and_disconnect_release_pending_request():
    manager, socket = ConnectionManager(), Socket()
    await manager.connect("session", socket)
    manager.layout_message("session", socket, {"type": "layout_ready", "ready": True})
    with pytest.raises(RuntimeError, match="timed out"):
        await manager.request_layout("session", {"action": "inspect"}, timeout=0.001)
    assert not manager._layout_pending
    await socket.messages.get()
    task = asyncio.create_task(manager.request_layout("session", {"action": "inspect"}))
    await socket.messages.get()
    await manager.disconnect("session", socket)
    with pytest.raises(RuntimeError, match="disconnected"):
        await task
    assert not manager._layout_pending


def test_layout_module_guidance_and_metadata_surface():
    assert {action.id for action in MODULE.actions} == {"inspect", "apply", "undo"}
    assert "wait for the user's agreement" in MODULE.description
    assert "never pane contents" in MODULE.description


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["inspect", "apply", "undo"])
async def test_registered_layout_tool_receives_current_session(monkeypatch, action):
    from uuid import uuid4

    from app.services.modules.builtins.session_layout import module
    from app.services.tools.executor import ToolExecutor
    from app.services.tools.registry import ToolRuntimeContext
    from app.services.tools.registry_builder import build_default_registry

    session_id = uuid4()
    calls = []

    class Manager:
        async def request_layout(self, session, command):
            calls.append((session, command))
            return {"ok": True, "panes": []}

    monkeypatch.setattr(module, "get_ws_manager", lambda: Manager())
    payload = {"action": action}
    if action == "apply":
        payload["operations"] = [{"operation": "restore"}]
    result, _ = await ToolExecutor(build_default_registry()).execute(
        "session_layout",
        payload,
        runtime=ToolRuntimeContext(session_id=session_id),
    )
    assert result["ok"]
    assert calls[0][0] == str(session_id)


def test_dedicated_layout_socket_replies_without_chat_receive_loop(monkeypatch):
    from uuid import uuid4

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import ws

    app = FastAPI()
    manager = ConnectionManager()
    app.state.ws_manager = manager
    app.include_router(ws.router)
    session_id = str(uuid4())

    class DB:
        async def commit(self):
            pass

    async def database():
        yield DB()

    async def session(*args):
        return object()

    app.dependency_overrides[ws.get_db] = database
    monkeypatch.setattr(ws, "get_session_record", session)

    async def request():
        async with asyncio.timeout(2):
            while not manager._layout_clients.get(session_id):
                await asyncio.sleep(0)
            return await manager.request_layout(session_id, {"action": "inspect"})

    with TestClient(app) as client:
        with client.websocket_connect(f"/{session_id}/layout") as socket:
            socket.send_json({"type": "layout_ready", "ready": True})
            future = client.portal.start_task_soon(request)
            message = socket.receive_json()
            assert message["type"] == "layout_request"
            socket.send_json(
                {
                    "type": "layout_result",
                    "request_id": message["request_id"],
                    "result": {"ok": True, "panes": []},
                }
            )
            assert future.result(timeout=2)["ok"]
            assert manager.get_active_count(session_id) == 0
