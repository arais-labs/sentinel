from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.sentral import TextBlock
from app.services.agent import ToolAdapter
from app.services.agent.sentinel_runner import PreparedRuntimeTurnContext
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import (
    AgentEvent,
    TokenUsage,
)
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry
from app.services.telegram.bridge import TelegramBridge
from tests.fake_db import FakeDB


class _DBFactory:
    def __init__(self, db: FakeDB):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _WSManager:
    def __init__(self) -> None:
        self.message_acks: list[dict] = []
        self.agent_events: list[dict] = []
        self.errors: list[dict] = []
        self.done: list[dict] = []
        self.thinking: list[str] = []

    async def broadcast_message_ack(self, session_key, message_id, content, created_at, metadata=None):
        self.message_acks.append(
            {
                "session_key": session_key,
                "message_id": message_id,
                "content": content,
                "created_at": created_at,
                "metadata": metadata or {},
            }
        )

    async def broadcast_agent_thinking(self, session_key: str) -> None:
        self.thinking.append(session_key)

    async def broadcast_agent_event(self, session_key: str, event) -> None:
        self.agent_events.append({"session_key": session_key, "event": event})

    async def broadcast_agent_error(self, session_key: str, message: str) -> None:
        self.errors.append({"session_key": session_key, "message": message})

    async def broadcast_done(self, session_key: str, stop_reason: str) -> None:
        self.done.append({"session_key": session_key, "stop_reason": stop_reason})


class _RunRegistry:
    async def is_running(self, _session_key: str) -> bool:
        return False

    async def register(self, _session_key: str, _task) -> bool:
        return True

    async def clear(self, _session_key: str, _task) -> None:
        return None


class _FakeProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "fake"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "tools": list(tools or []),
                "temperature": temperature,
                "stream": False,
            }
        )
        raise AssertionError("Telegram runtime adapter should use streaming provider path in this test")

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "tools": list(tools or []),
                "temperature": temperature,
                "stream": True,
            }
        )
        yield AgentEvent(type="text_delta", delta="reply")
        yield AgentEvent(type="done", stop_reason="stop")


class _AgentLoopStub:
    def __init__(self) -> None:
        self.provider = _FakeProvider()
        self._estop = SimpleNamespace(check_level=AsyncMock(return_value=None))
        self.context_builder = SimpleNamespace(build=AsyncMock(return_value=[]))
        registry = ToolRegistry()
        self.tool_adapter = ToolAdapter(registry, ToolExecutor(registry))
        self.persist_calls: list[dict] = []

    async def estop_level(self, db):
        return await self._estop.check_level(db)

    async def prepare_runtime_turn_context(
        self,
        db,
        session_id,
        *,
        system_prompt,
        pending_user_message,
        agent_mode,
        model,
        temperature,
        max_iterations,
        stream,
    ):
        messages = await self.context_builder.build(db, session_id, system_prompt, pending_user_message, agent_mode)
        return PreparedRuntimeTurnContext(
            messages=messages,
            tools=self.tool_adapter.get_tool_schemas(),
            effective_system_prompt=system_prompt,
            runtime_context_snapshot=None,
        )

    async def persist_created_messages(self, db, session_id, created, assistant_iterations, **kwargs):
        await self._persist_messages(db, session_id, created, assistant_iterations, **kwargs)

    async def _persist_messages(self, db, session_id, created, assistant_iterations, **kwargs) -> None:
        self.persist_calls.append(
            {
                "db": db,
                "session_id": session_id,
                "created": list(created),
                "assistant_iterations": dict(assistant_iterations),
                "kwargs": kwargs,
            }
        )

    def _extract_final_text(self, _messages) -> str:
        return "reply"

    @staticmethod
    def _collect_attachments(_messages) -> list[dict]:
        return []

    def extract_final_text(self, messages) -> str:
        return self._extract_final_text(messages)

    def collect_attachments(self, messages) -> list[dict]:
        return self._collect_attachments(messages)


def _build_bridge(db: FakeDB) -> TelegramBridge:
    return TelegramBridge(
        bot_token="dummy",
        user_id="user-1",
        agent_loop=_AgentLoopStub(),
        run_registry=_RunRegistry(),
        ws_manager=_WSManager(),
        db_factory=lambda: _DBFactory(db),
    )


def test_telegram_process_message_uses_runtime_adapter_and_preserves_ws_events():
    db = FakeDB()
    bridge = _build_bridge(db)
    session_id = uuid4()
    route = SimpleNamespace(
        session_id=session_id,
        session_key=str(session_id),
        route_scope="group_channel",
        inline_reply_mode=False,
        chat_id=123,
        chat_type="group",
    )
    persisted = SimpleNamespace(
        message=SimpleNamespace(id=uuid4(), content="hello", created_at=datetime.now(UTC)),
        is_first_message=False,
    )
    update = SimpleNamespace(
        message=SimpleNamespace(reply_text=AsyncMock(), text="hello", caption=None),
        effective_chat=SimpleNamespace(id=123, type="group"),
        effective_user=SimpleNamespace(id=456, full_name="User"),
    )

    with (
        patch.object(bridge, "_resolve_route_context", new=AsyncMock(return_value=route)),
        patch.object(bridge, "_wait_for_session_ready", new=AsyncMock(return_value=True)),
        patch.object(bridge, "_persist_inbound_user_message", new=AsyncMock(return_value=persisted)),
        patch.object(bridge, "_deliver_non_inline_reply", new=AsyncMock(return_value=None)) as deliver_mock,
        patch.object(bridge, "_auto_compact_after_run", new=AsyncMock(return_value=None)),
    ):
        asyncio.run(bridge._process_message(update, {"telegram_user_id": "456"}))  # noqa: SLF001

    ws = bridge._ws_manager  # noqa: SLF001
    assert ws.message_acks
    assert ws.thinking == [str(session_id)]
    assert [entry["event"].type for entry in ws.agent_events] == [
        "agent_progress",
        "text_delta",
        "done",
    ]
    deliver_mock.assert_awaited_once()
    agent_loop = bridge._agent_loop  # noqa: SLF001
    assert agent_loop.provider.calls
    first_messages = agent_loop.provider.calls[0]["messages"]
    assert first_messages[-1].content == "hello"
