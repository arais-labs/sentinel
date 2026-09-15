import asyncio
import base64
import shlex
import sys
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from sentral.llm.runtime_conversions import sentinel_message_to_runtime_item
from sentral.llm.runtime_adapter import SentinelProviderAdapter
from sentral.llm.generic.types import ImageContent, ToolResultMessage, UserMessage
from tests.test_runtime_desktop_cleanup import desktop_manager

path = Path(__file__).parents[1] / "app/services/runtime/guest_commands/linux/desktop/computer.py"
spec = importlib.util.spec_from_file_location("workspace_computer_worker", path)
worker = importlib.util.module_from_spec(spec)
# X11/Pillow are guest dependencies; controller validation needs no host display.
xlib = ModuleType("Xlib")
xlib.X, xlib.XK, xlib.display = SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
xlib_ext = ModuleType("Xlib.ext")
xlib_ext.xtest = SimpleNamespace()
pillow = ModuleType("PIL")
pillow.Image = SimpleNamespace()
with patch.dict(sys.modules, {"Xlib": xlib, "Xlib.ext": xlib_ext, "PIL": pillow}):
    spec.loader.exec_module(worker)


@pytest.mark.parametrize(
    "actions",
    [
        [{"type": "move", "x": 800, "y": 0}],
        [{"type": "click", "x": True, "y": 0}],
        [{"type": "scroll", "x": 1, "y": 1, "direction": "down", "ticks": 1000}],
        [{"type": "keypress", "keys": ["--window"]}],
        [{"type": "type", "text": "x\x00"}],
        [{"type": "wait", "milliseconds": 30000}],
        [{"type": "exec", "code": "anything"}],
        [{"type": "drag", "path": [{"x": 1, "y": 1}]}],
        [{"type": "move", "x": 1, "y": 1}] * 17,
    ],
)
def test_invalid_action_batches_rejected(actions):
    with pytest.raises(ValueError):
        worker.validate({"actions": actions, "viewport": {"width": 800, "height": 600}}, 800, 600)


def test_resize_requires_fresh_observation():
    with pytest.raises(ValueError, match="dimensions"):
        worker.validate(
            {
                "actions": [{"type": "click", "x": 2, "y": 2}],
                "viewport": {"width": 900, "height": 600},
            },
            800,
            600,
        )
    assert worker.validate({}, 800, 600) == []


@pytest.mark.asyncio
async def test_computer_uses_bound_container_and_preserves_partial_failure():
    manager, transport = desktop_manager()
    manager.ensure_session_desktop = AsyncMock()
    payload = {"ok": False, "completed_actions": 1, "error": "resized", "screenshot": "image"}
    transport.run = AsyncMock(
        return_value=SimpleNamespace(stdout=json.dumps(payload), exit_status=0)
    )
    request = {
        "actions": [{"type": "type", "text": "$(do not execute)"}],
        "viewport": {"width": 800, "height": 600},
    }
    assert await manager.computer("session", request) == payload
    manager.ensure_session_desktop.assert_awaited_once_with("session")
    args = shlex.split(transport.run.call_args.args[0])
    assert args[:2] == ["python3", "-c"]
    sent = json.loads(args[3])
    assert sent.pop("request_id")
    assert sent == request
    assert "$(do not execute)" not in args[2]
    assert 'display.Display(":1")' in args[2]


@pytest.mark.asyncio
async def test_no_host_fallback_without_desktop_package():
    manager, transport = desktop_manager(tools=())
    with pytest.raises(RuntimeError, match="Desktop"):
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
    monkeypatch.setattr(handlers, "get_runtime_desktop_manager", resolver)
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


def test_release_runs_when_drag_action_is_interrupted():
    desktop = object.__new__(worker.Desktop)
    desktop.X = SimpleNamespace(ButtonPress=4)
    desktop.press = lambda *args: None
    released = []
    desktop.release = lambda: released.append(True)

    def bad_move(point):
        if point["x"] == 2:
            raise TimeoutError("cancelled")

    desktop.move = bad_move
    with pytest.raises(TimeoutError):
        desktop.execute({"type": "drag", "path": [{"x": 1, "y": 1}, {"x": 2, "y": 2}]})
    assert released == [True]


@pytest.mark.asyncio
async def test_cancel_signals_only_its_own_workspace_request():
    manager, transport = desktop_manager()
    manager.ensure_session_desktop = AsyncMock()
    started = asyncio.Event()
    released = asyncio.Event()
    calls = []

    async def run(command, *, timeout):
        parts = shlex.split(command)
        calls.append((parts[2], parts[3:]))
        if len(calls) == 1:
            started.set()
            await released.wait()
            return SimpleNamespace(stdout='{"ok":false,"error":"cancelled"}')
        released.set()
        return SimpleNamespace(stdout="")

    transport.run = run
    task = asyncio.create_task(manager.computer("session", {"actions": []}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) == 2
    assert calls[1][1] == [json.loads(calls[0][1][0])["request_id"]]
    assert "signal.SIGTERM" in calls[1][0]


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
