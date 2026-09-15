"""Transport recovery must preserve requests and never replay a sent generation."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response
from websockets.datastructures import Headers

from sentral.llm.generic.errors import is_retryable, status_code
from sentral.llm.generic.transport_context import provider_transport_session, transport_session
from sentral.llm.providers.codex import CodexProvider

DONE = 'data: {"type":"response.completed"}\n\n'


def handshake_error(code):
    return InvalidStatus(Response(code, "Unavailable", Headers()))


@pytest.mark.parametrize("code,retryable", [(503, True), (429, True), (401, False)])
def test_websocket_http_status_classification(code, retryable):
    exc = handshake_error(code)
    assert status_code(exc) == code
    assert is_retryable(exc) is retryable


@pytest.mark.asyncio
async def test_fallback_preserves_payload_and_is_scoped_to_chat(monkeypatch):
    contexts = []
    posts = []

    def connect(*args, **kwargs):
        ctx = AsyncMock()
        ctx.__aenter__.side_effect = handshake_error(503)
        contexts.append(ctx)
        return ctx

    def http(request):
        posts.append(request)
        return httpx.Response(200, text=DONE)

    monkeypatch.setattr("sentral.llm.providers.codex.connect", connect)
    monkeypatch.setattr("sentral.llm.providers.codex.asyncio.sleep", AsyncMock())
    provider = CodexProvider("test")
    payload = {
        "model": "gpt-5.6-sol",
        "input": [],
        "reasoning": {"effort": "high"},
        "service_tier": "priority",
        "tools": [{"type": "function", "name": "tool"}],
    }
    headers = {"authorization": "Bearer test", "ChatGPT-Account-ID": "account"}
    async with httpx.AsyncClient(transport=httpx.MockTransport(http)) as client:
        for session, expected_connects in [("one", 2), ("one", 2), ("two", 4)]:
            with provider_transport_session(session):
                lines = [x async for x in provider._stream_lines_once(client, payload, headers)]
            assert "response.completed" in lines[0]
            assert len(contexts) == expected_connects
    assert transport_session.get() is None
    assert len(posts) == 3
    for post in posts:
        assert json.loads(post.content) == payload
        assert post.headers["authorization"] == headers["authorization"]
        assert post.headers["ChatGPT-Account-ID"] == "account"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [400, 401, 403, 429])
async def test_request_errors_do_not_switch_transport(monkeypatch, code):
    ctx = AsyncMock()
    ctx.__aenter__.side_effect = handshake_error(code)
    monkeypatch.setattr("sentral.llm.providers.codex.connect", lambda *a, **kw: ctx)
    provider = CodexProvider("test")
    with pytest.raises(InvalidStatus):
        _ = [x async for x in provider._stream_lines_once(None, {}, {})]
    ctx.__aenter__.assert_awaited_once()
    assert not provider._http_fallback_sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["send", "recv", "cancel"])
async def test_no_replay_after_send_or_cancellation(monkeypatch, stage):
    socket = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__.return_value = socket
    if stage == "cancel":
        ctx.__aenter__.side_effect = asyncio.CancelledError()
    else:
        getattr(socket, stage).side_effect = OSError("disconnected")
    monkeypatch.setattr("sentral.llm.providers.codex.connect", lambda *a, **kw: ctx)
    provider = CodexProvider("test")
    with provider_transport_session("one"):
        with pytest.raises(asyncio.CancelledError if stage == "cancel" else OSError):
            _ = [x async for x in provider._stream_lines_once(None, {}, {})]
    assert not provider._http_fallback_sessions
    if stage != "cancel":
        ctx.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_transient_handshake_recovers_websocket_and_reuses_socket(monkeypatch):
    socket = AsyncMock()
    socket.recv.return_value = '{"type":"response.completed"}'
    failed = AsyncMock()
    failed.__aenter__.side_effect = handshake_error(503)
    connected = AsyncMock()
    connected.__aenter__.return_value = socket
    calls = []

    def connect(*a, **kw):
        calls.append(kw)
        return failed if len(calls) == 1 else connected

    monkeypatch.setattr("sentral.llm.providers.codex.connect", connect)
    monkeypatch.setattr("sentral.llm.providers.codex.asyncio.sleep", AsyncMock())
    provider = CodexProvider("test")
    try:
        for _ in range(2):
            assert [x async for x in provider._stream_lines_once(None, {}, {})]
        assert len(calls) == 2
        assert socket.send.await_count == 2
        assert not provider._http_fallback_sessions
    finally:
        await provider.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(), OSError(), handshake_error(426)])
async def test_connection_failure_falls_back(monkeypatch, error):
    ctx = AsyncMock()
    ctx.__aenter__.side_effect = error
    monkeypatch.setattr("sentral.llm.providers.codex.connect", lambda *a, **kw: ctx)
    monkeypatch.setattr("sentral.llm.providers.codex.asyncio.sleep", AsyncMock())
    provider = CodexProvider("test")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=DONE))
    ) as client:
        assert [x async for x in provider._stream_lines_once(client, {}, {})]
    assert ctx.__aenter__.await_count == (1 if status_code(error) == 426 else 2)


@pytest.mark.asyncio
async def test_runtime_sets_and_resets_chat_transport_context():
    from uuid import uuid4
    from app.services.agent_runtime_adapters.runtime import SentinelLoopRuntimeAdapter

    runtime = object.__new__(SentinelLoopRuntimeAdapter)
    runtime._session_id = uuid4()

    async def execute(*args, **kwargs):
        assert transport_session.get() == str(runtime._session_id)
        raise RuntimeError("failed turn")

    runtime._execute_in_transport_session = execute
    with provider_transport_session("parent"):
        with pytest.raises(RuntimeError, match="failed turn"):
            await runtime._execute(None)
        assert transport_session.get() == "parent"
    assert transport_session.get() is None
