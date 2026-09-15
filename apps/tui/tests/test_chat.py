import asyncio
import sys

import pytest
from sentral import AgentEvent, ConversationItem, TextBlock
from sentinel_tui.tools import Tools
from sentinel_tui.ui import Chat, Approval


@pytest.mark.asyncio
async def test_deny_prevents_process_launch():
    async def deny(*_):
        return False

    tools = Tools(deny)
    result = await tools.get_tool("host_runtime_exec").execute({"shell_command": "exit 99"})
    assert result.status == "error"
    assert not tools.processes.jobs
    await tools.close()


@pytest.mark.asyncio
async def test_real_owned_process_exec_poll_list_and_cleanup():
    async def approve(*_):
        return True

    tools = Tools(approve)
    result = await tools.get_tool("host_runtime_exec").execute(
        {"shell_command": "printf hello", "login": False}
    )
    assert result.status == "ok", result.error
    assert result.content["stdout"] == "hello"
    listing = await tools.get_tool("host_runtime_list").execute({})
    assert len(listing.content["processes"]) == 1
    await tools.close()
    assert not tools.processes.jobs


class Provider:
    name = "fake"

    async def stream(self, **kwargs):
        yield AgentEvent(type="text_delta", delta="Hello")
        yield AgentEvent(
            type="done",
            item=ConversationItem("answer", "assistant", [TextBlock(text="Hello")]),
            stop_reason="stop",
        )


@pytest.mark.asyncio
async def test_headless_stream_and_new_chat():
    app = Chat(Provider(), "fake")
    async with app.run_test() as pilot:
        await pilot.press("h", "i", "enter")
        await pilot.pause()
        await app.turn
        assert any(item.role == "assistant" for item in app.history)
        assert app.query("Markdown")
        await pilot.press("/", "n", "e", "w", "enter")
        await pilot.pause()
        assert app.history == []
    assert not any(
        name.startswith(("app.models", "app.config", "fastapi", "sqlalchemy"))
        for name in sys.modules
    )


@pytest.mark.asyncio
async def test_approval_modal_denial():
    app = Chat(Provider(), "fake")
    async with app.run_test() as pilot:
        decision = asyncio.create_task(
            app.approve("host_runtime_exec", {"shell_command": "echo test"})
        )
        await pilot.pause()
        assert isinstance(app.screen, Approval)
        await pilot.click("#deny")
        assert await decision is False


@pytest.mark.asyncio
async def test_steering_and_cancel_preserve_input():
    entered = asyncio.Event()

    class Waiting(Provider):
        async def stream(self, **kwargs):
            entered.set()
            await asyncio.Event().wait()
            yield

    app = Chat(Waiting(), "fake")
    async with app.run_test() as pilot:
        await pilot.press("h", "i", "enter")
        await entered.wait()
        await pilot.press("s", "t", "e", "e", "r", "enter")
        await pilot.pause()
        assert app.pending[0].content[0].text == "steer"
        app.action_cancel()
        await app.turn
        assert any(item.content[0].text == "steer" for item in app.history)


@pytest.mark.asyncio
async def test_http_uses_shared_handler_and_approval(monkeypatch):
    import httpx
    from sentral.tools import http_request

    calls = []

    async def approved(name, payload):
        calls.append(name)
        return True

    async def public(host):
        assert host == "example.test"

    client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(http_request, "_validate_public_hostname", public)
    monkeypatch.setattr(
        http_request.httpx, "AsyncClient", lambda **kw: client(transport=transport, **kw)
    )
    tools = Tools(approved)
    result = await tools.get_tool("http_request").execute({"url": "https://example.test"})
    assert result.status == "ok"
    assert result.content["body"] == {"ok": True}
    assert calls == ["http_request"]
    await tools.close()
