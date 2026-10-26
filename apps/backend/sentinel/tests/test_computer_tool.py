import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from sentral.llm.runtime_conversions import sentinel_message_to_runtime_item
from sentral.llm.runtime_adapter import SentinelProviderAdapter
from sentral.llm.generic.types import ImageContent, ToolResultMessage, UserMessage
from tests.test_runtime_desktop_cleanup import desktop_manager


@pytest.mark.asyncio
async def test_computer_is_the_sentineld_op_and_preserves_partial_failure(monkeypatch):
    manager, transport = desktop_manager()
    manager.ensure_session_desktop = AsyncMock()
    payload = {"ok": False, "completed_actions": 1, "error": "resized", "screenshot": "image"}
    request = AsyncMock(return_value={"batch": payload})
    monkeypatch.setattr("app.services.workspaces.desktop.manager.lifecycle.request", request)
    batch = {
        "actions": [{"type": "type", "text": "$(do not execute)"}],
        "viewport": {"width": 800, "height": 600},
    }
    assert await manager.computer("session", batch) == payload
    manager.ensure_session_desktop.assert_awaited_once_with("session")
    request.assert_awaited_once_with(
        "computer", workspace=str(manager._workspace_location.workspace_id), **batch
    )
    transport.run.assert_not_awaited()  # nothing runs in the guest from here


@pytest.mark.asyncio
async def test_no_host_fallback_without_desktop_package():
    manager, transport = desktop_manager(desktop="none")
    with pytest.raises(RuntimeError, match="Choose a desktop"):
        await manager.computer("session", {"actions": []})
    transport.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_module_routes_runtime_session_and_instance(monkeypatch):
    from app.services.modules.builtins.computer import handlers

    factory = object()
    session = uuid4()
    runtime = SimpleNamespace(
        runtime_session_id=session,
        session_id=uuid4(),
        instance_name="test",
        db_session_factory=factory,
    )
    manager = SimpleNamespace(computer=AsyncMock(return_value={"ok": True}))
    resolver = AsyncMock(return_value=manager)
    monkeypatch.setattr(handlers, "get_desktop_manager", resolver)
    await handlers.handle_screenshot({}, runtime)
    resolver.assert_awaited_once_with(
        session_id=str(session), instance_name="test", session_factory=factory
    )
    manager.computer.assert_awaited_once_with(str(session), {"actions": []})


def test_latest_tool_screenshot_reaches_model_as_image_not_base64_text():
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"X" * 100).decode()
    tool = ToolResultMessage(
        tool_call_id="c1",
        tool_name="computer",
        content="Screenshot attached",
        metadata={"attachments": [{"base64": png, "mime_type": "image/png", "size_bytes": 108}]},
    )
    item = sentinel_message_to_runtime_item(tool, item_id="t1")
    messages = SentinelProviderAdapter(None)._messages([item])
    assert isinstance(messages[0], ToolResultMessage)
    assert isinstance(messages[1], UserMessage)
    assert any(isinstance(x, ImageContent) and x.data == png for x in messages[1].content)
    new = sentinel_message_to_runtime_item(UserMessage(content="New task"), item_id="u1")
    assert len(SentinelProviderAdapter(None)._messages([item, new])) == 2


@pytest.mark.asyncio
async def test_cancel_stops_only_its_own_workspace_batch(monkeypatch):
    manager, _ = desktop_manager()
    manager.ensure_session_desktop = AsyncMock()
    started, released = asyncio.Event(), asyncio.Event()
    calls = []

    async def request(action, **values):
        calls.append((action, values))
        if action == "computer":
            started.set()
            await released.wait()
            return {"batch": {"ok": False, "error": "cancelled"}}
        released.set()
        return {"cancelled": True}

    monkeypatch.setattr("app.services.workspaces.desktop.manager.lifecycle.request", request)
    task = asyncio.create_task(manager.computer("session", {"actions": []}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    workspace = str(manager._workspace_location.workspace_id)
    assert calls == [
        ("computer", {"workspace": workspace, "actions": []}),
        ("computer_cancel", {"workspace": workspace}),
    ]


def test_screenshot_is_an_image_in_codex_request():
    from app.services.agent.attachments import extract_attachments
    from sentral.llm.providers.codex import CodexProvider

    data = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"X" * 100).decode()
    attachments = []
    cleaned = extract_attachments(
        {"screenshot": "data:image/png;base64," + data}, attachments=attachments
    )
    result = ToolResultMessage(
        tool_call_id="c1",
        tool_name="computer",
        content=json.dumps(cleaned),
        metadata={"attachments": attachments},
    )
    messages = SentinelProviderAdapter(None)._messages(
        [sentinel_message_to_runtime_item(result, item_id="t1")]
    )
    _, items = CodexProvider("test")._to_responses_input(messages)
    assert items[0]["type"] == "function_call_output"
    assert data not in items[0]["output"]
    assert any(
        part.get("type") == "input_image" and part["image_url"].endswith(data)
        for part in items[-1]["content"]
    )
