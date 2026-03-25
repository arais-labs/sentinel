from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from uuid import uuid4

import pytest

from app.sentral import ConversationItem, GenerationConfig, RunTurnRequest, TextBlock
from app.services.agent import ToolAdapter
from app.services.agent.sentinel_runner import PreparedRuntimeTurnContext
from app.services.agent_runtime_adapters import SentinelLoopRuntimeAdapter
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent, AssistantMessage, TextContent, TokenUsage, ToolCallContent
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolDefinition, ToolRegistry, ToolRuntimeContext


@dataclass
class _FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0


class _FakeEstop:
    async def check_level(self, _db):
        return None


class _FakeContextBuilder:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def build(self, db, session_id, system_prompt, pending_user_message, agent_mode):
        self.calls.append(
            {
                "db": db,
                "session_id": session_id,
                "system_prompt": system_prompt,
                "pending_user_message": pending_user_message,
                "agent_mode": agent_mode,
            }
        )
        return []


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
                "tools": list(tools or []),
                "model": model,
                "temperature": temperature,
            }
        )
        return AssistantMessage(
            content=[TextContent(text="hello")],
            model="fake-model",
            provider="fake",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="stop",
        )

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools or []),
                "model": model,
                "temperature": temperature,
                "stream": True,
            }
        )
        yield AgentEvent(type="text_delta", delta="hello")
        yield AgentEvent(type="done", stop_reason="stop")


class _FakeLoop:
    def __init__(self) -> None:
        self._estop = _FakeEstop()
        self.context_builder = _FakeContextBuilder()
        registry = ToolRegistry()
        self.tool_adapter = ToolAdapter(registry, ToolExecutor(registry))
        self.provider = _FakeProvider()
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

    def _extract_final_text(self, messages) -> str:
        for message in reversed(messages):
            if isinstance(message, AssistantMessage):
                return "\n".join(
                    block.text
                    for block in message.content
                    if isinstance(block, TextContent) and block.text
                )
        return ""

    @staticmethod
    def _collect_attachments(_messages) -> list[dict]:
        return []

    def extract_final_text(self, messages) -> str:
        return self._extract_final_text(messages)

    def collect_attachments(self, messages) -> list[dict]:
        return self._collect_attachments(messages)


@pytest.mark.asyncio
async def test_runtime_adapter_run_turn_uses_bound_session_and_returns_history() -> None:
    session_id = uuid4()
    loop = _FakeLoop()
    expected_history = [
        ConversationItem(
            id="assistant-1",
            role="assistant",
            content=[TextBlock(text="hello")],
            metadata={"provider": "fake"},
        )
    ]

    async def _history_loader(db, loaded_session_id):
        assert loaded_session_id == session_id
        return expected_history

    adapter = SentinelLoopRuntimeAdapter(
        loop=loop,
        db=object(),
        session_id=session_id,
        history_loader=_history_loader,
    )

    result = await adapter.run_turn(
        RunTurnRequest(
            conversation_id=str(session_id),
            new_items=[
                ConversationItem(
                    id="user-1",
                    role="user",
                    content=[TextBlock(text="hello")],
                )
            ],
            config=GenerationConfig(model="normal", max_iterations=8),
        )
    )

    assert result.status == "completed"
    assert result.history == expected_history
    assert result.final_item == expected_history[0]
    assert result.stop_reason == "stop"
    assert loop.context_builder.calls[0]["session_id"] == session_id
    assert loop.context_builder.calls[0]["pending_user_message"] == "hello"
    assert loop.provider.calls
    first_messages = loop.provider.calls[0]["messages"]
    assert first_messages[-1].content == "hello"


@pytest.mark.asyncio
async def test_runtime_adapter_stream_turn_yields_runtime_events() -> None:
    session_id = uuid4()
    adapter = SentinelLoopRuntimeAdapter(
        loop=_FakeLoop(),
        db=object(),
        session_id=session_id,
        history_loader=_empty_history_loader,
    )

    events = []
    async for event in adapter.stream_turn(
        RunTurnRequest(
            conversation_id=str(session_id),
            new_items=[
                ConversationItem(
                    id="user-1",
                    role="user",
                    content=[TextBlock(text="hello")],
                )
            ],
            config=GenerationConfig(model="normal"),
        )
    ):
        events.append(event)

    assert [event.type for event in events] == [
        "agent_progress",
        "text_delta",
        "done",
    ]
    assert events[-1].stop_reason == "stop"


@pytest.mark.asyncio
async def test_runtime_adapter_rejects_direct_history_for_now() -> None:
    session_id = uuid4()
    adapter = SentinelLoopRuntimeAdapter(
        loop=_FakeLoop(),
        db=object(),
        session_id=session_id,
        history_loader=_empty_history_loader,
    )

    with pytest.raises(NotImplementedError):
        await adapter.run_turn(
            RunTurnRequest(
                history=[],
                new_items=[
                    ConversationItem(
                        id="user-1",
                        role="user",
                        content=[TextBlock(text="hello")],
                    )
                ],
                config=GenerationConfig(model="normal"),
            )
        )


@pytest.mark.asyncio
async def test_runtime_adapter_cancellation_persists_partial_tool_results() -> None:
    session_id = uuid4()
    loop = _FakeLoop()
    fast_done = asyncio.Event()
    slow_started = asyncio.Event()

    async def _fast_tool(_payload: dict, runtime: ToolRuntimeContext):
        assert runtime.session_id == session_id
        fast_done.set()
        return {"tool": "fast"}

    async def _slow_tool(_payload: dict, runtime: ToolRuntimeContext):
        assert runtime.session_id == session_id
        slow_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        raise AssertionError("slow tool should have been cancelled")

    loop.tool_adapter.registry.register(
        ToolDefinition(
            name="fast_tool",
            description="fast",
            parameters_schema={"type": "object"},
            execute=_fast_tool,
        )
    )
    loop.tool_adapter.registry.register(
        ToolDefinition(
            name="slow_tool",
            description="slow",
            parameters_schema={"type": "object"},
            execute=_slow_tool,
        )
    )
    loop.provider = _ToolUseProvider()

    adapter = SentinelLoopRuntimeAdapter(
        loop=loop,
        db=object(),
        session_id=session_id,
        history_loader=_empty_history_loader,
    )

    async def _scenario():
        task = asyncio.create_task(
            adapter.run_turn(
                RunTurnRequest(
                    conversation_id=str(session_id),
                    new_items=[
                        ConversationItem(
                            id="user-1",
                            role="user",
                            content=[TextBlock(text="run tools")],
                        )
                    ],
                    config=GenerationConfig(model="normal", stream=True, max_iterations=4),
                )
            )
        )
        await asyncio.wait_for(fast_done.wait(), timeout=1.0)
        await asyncio.wait_for(slow_started.wait(), timeout=1.0)
        task.cancel()
        return await asyncio.wait_for(task, timeout=1.0)

    result = await _scenario()

    assert result.status == "aborted"
    assert result.stop_reason == "aborted"

    persisted = loop.persist_calls[-1]["created"]
    assert [message.role for message in persisted] == ["user", "assistant", "tool_result", "tool_result"]
    fast_result = next(message for message in persisted if message.role == "tool_result" and message.tool_call_id == "call_fast")
    slow_result = next(message for message in persisted if message.role == "tool_result" and message.tool_call_id == "call_slow")

    assert json.loads(fast_result.content)["tool"] == "fast"
    slow_payload = json.loads(slow_result.content)
    assert slow_payload["status"] == "cancelled"
    assert slow_result.metadata["cancelled_by_stop"] is True


async def _empty_history_loader(_db, _session_id):
    return []


class _ToolUseProvider(_FakeProvider):
    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        raise AssertionError("chat() should not be called in this test")

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools or []),
                "model": model,
                "temperature": temperature,
                "stream": True,
            }
        )
        yield AgentEvent(
            type="toolcall_start",
            tool_call=ToolCallContent(id="call_fast", name="fast_tool", arguments={"label": "fast"}),
        )
        yield AgentEvent(
            type="toolcall_start",
            tool_call=ToolCallContent(id="call_slow", name="slow_tool", arguments={"label": "slow"}),
        )
        yield AgentEvent(type="done", stop_reason="tool_use")
