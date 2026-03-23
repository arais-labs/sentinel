from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.services.llm.generic.types import AgentEvent, ToolCallContent, ToolResultContent
from app.services.ws.ws_manager import ConnectionManager


def _run(coro):
    return asyncio.run(coro)


class _Socket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        if self.fail:
            raise RuntimeError("send failed")
        self.messages.append(payload)


def test_connection_manager_typed_broadcast_methods():
    manager = ConnectionManager()
    socket = _Socket()

    async def _scenario():
        await manager.connect("session-1", socket)
        await manager.broadcast_message_ack("session-1", "msg-1", "hello", datetime.now(UTC))
        await manager.broadcast_agent_thinking("session-1")
        await manager.broadcast_agent_error("session-1", "no provider")
        await manager.broadcast_done("session-1", "error")

    _run(_scenario())
    assert [item["type"] for item in socket.messages] == [
        "message_ack",
        "agent_thinking",
        "agent_error",
        "done",
    ]


def test_broadcast_agent_event_converts_all_event_types():
    manager = ConnectionManager()
    socket = _Socket()

    events = [
        AgentEvent(type="start"),
        AgentEvent(type="text_start", content_index=0),
        AgentEvent(type="text_delta", delta="hello", content_index=0),
        AgentEvent(type="text_end", content_index=0),
        AgentEvent(type="thinking_start", content_index=1),
        AgentEvent(type="thinking_delta", delta="hmm", content_index=1),
        AgentEvent(type="thinking_end", content_index=1),
        AgentEvent(type="toolcall_start", tool_call=ToolCallContent(id="c1", name="tool", arguments={"x": 1})),
        AgentEvent(type="toolcall_delta", delta="{", content_index=2),
        AgentEvent(type="toolcall_end", content_index=2),
        AgentEvent(
            type="tool_result",
            tool_result=ToolResultContent(
                tool_call_id="c1",
                tool_name="tool",
                content='{"ok":true}',
                is_error=False,
                metadata={"source": "test"},
                tool_arguments={"x": 1},
            ),
        ),
        AgentEvent(type="done", stop_reason="stop"),
        AgentEvent(type="error", error="boom"),
    ]

    async def _scenario():
        await manager.connect("session-2", socket)
        for event in events:
            await manager.broadcast_agent_event("session-2", event)

    _run(_scenario())
    assert len(socket.messages) == 13
    assert [item["type"] for item in socket.messages] == [event.type for event in events]
    assert socket.messages[2]["delta"] == "hello"
    assert socket.messages[7]["tool_call"]["name"] == "tool"
    assert socket.messages[10]["tool_result"]["tool_arguments"] == {"x": 1}
    assert socket.messages[11]["stop_reason"] == "stop"
    assert socket.messages[12]["error"] == "boom"


def test_sub_agent_events_payloads():
    manager = ConnectionManager()
    socket = _Socket()

    async def _scenario():
        await manager.connect("session-3", socket)
        await manager.broadcast_sub_agent_started("session-3", "task-1", "collect logs")
        await manager.broadcast_sub_agent_completed("session-3", "task-1", "completed", {"final_text": "ok"})

    _run(_scenario())
    assert socket.messages[0] == {
        "type": "sub_agent_started",
        "session_id": "session-3",
        "task_id": "task-1",
        "objective": "collect logs",
    }
    assert socket.messages[1]["type"] == "sub_agent_completed"
    assert socket.messages[1]["status"] == "completed"


def test_disconnect_on_send_failure():
    manager = ConnectionManager()
    good = _Socket()
    bad = _Socket(fail=True)

    async def _scenario():
        await manager.connect("session-4", good)
        await manager.connect("session-4", bad)
        await manager.broadcast_done("session-4", "stop")

    _run(_scenario())
    assert manager.get_active_count("session-4") == 1
    assert good.messages and good.messages[0]["type"] == "done"
