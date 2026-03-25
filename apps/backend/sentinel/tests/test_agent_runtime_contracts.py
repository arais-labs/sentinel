from __future__ import annotations

import asyncio
import json
from typing import assert_type

import pytest

from app.sentral import (
    AgentEvent,
    AgentRuntimeEngine,
    ApprovalRequest,
    CompactionConfig,
    ConversationItem,
    GenerationConfig,
    InMemoryConversationStore,
    RunTurnRequest,
    TextBlock,
    ToolCallBlock,
    ToolDefinition,
    ToolExecutionResult,
    TurnResult,
)


@pytest.mark.asyncio
async def test_in_memory_conversation_store_append_and_replace() -> None:
    store = InMemoryConversationStore()
    original = ConversationItem(
        id="user-1",
        role="user",
        content=[TextBlock(text="hello")],
    )
    follow_up = ConversationItem(
        id="assistant-1",
        role="assistant",
        content=[TextBlock(text="hi")],
    )

    await store.append_items("conv-1", [original])
    assert await store.load_history("conv-1") == [original]

    await store.append_items("conv-1", [follow_up])
    assert await store.load_history("conv-1") == [original, follow_up]

    await store.replace_history("conv-1", [follow_up])
    assert await store.load_history("conv-1") == [follow_up]


def test_run_turn_request_supports_history_or_conversation_store_flow() -> None:
    request = RunTurnRequest(
        conversation_id="conv-1",
        history=[
            ConversationItem(
                id="user-1",
                role="user",
                content=[TextBlock(text="hello")],
            )
        ],
        new_items=[
            ConversationItem(
                id="user-2",
                role="user",
                content=[TextBlock(text="follow up")],
            )
        ],
        config=GenerationConfig(model="normal", stream=True, max_iterations=8),
    )

    assert request.conversation_id == "conv-1"
    assert len(request.history or []) == 1
    assert len(request.new_items) == 1
    assert request.config is not None
    assert request.config.model == "normal"


def test_turn_result_can_represent_pending_approval() -> None:
    approval = ApprovalRequest(
        id="approval-1",
        tool_name="browser",
        action="browser.navigate",
        description="Navigate to a URL.",
        payload={"url": "https://example.com"},
    )
    result = TurnResult(
        status="pending_approval",
        history=[],
        pending_approval=approval,
        stop_reason="pending_approval",
        iterations=1,
    )

    assert result.status == "pending_approval"
    assert result.pending_approval == approval
    assert result.stop_reason == "pending_approval"


def test_tool_execution_result_supports_pending_approval_status() -> None:
    result = ToolExecutionResult(
        status="pending_approval",
        approval_request=ApprovalRequest(
            id="approval-1",
            tool_name="git_push",
            action="git.push",
            description="Push current branch to origin.",
        ),
    )

    assert result.status == "pending_approval"
    assert result.approval_request is not None
    assert result.approval_request.tool_name == "git_push"


def test_types_expose_expected_shapes() -> None:
    config = CompactionConfig(target_token_count=8000, model="normal")
    assert_type(config.target_token_count, int)


def test_engine_assemble_turn_ignores_tool_calls_without_ids() -> None:
    engine = AgentRuntimeEngine(provider=_NoopProvider(), tool_registry=_EmptyToolRegistry())

    turn = engine._assemble_turn_from_events(
        [
            AgentEvent(
                type="toolcall_start",
                tool_call=ToolCallBlock(id="", name="runtime_exec", arguments={"command": "pwd"}),
            ),
            AgentEvent(type="toolcall_delta", delta='{"command":"pwd"}'),
            AgentEvent(type="done", stop_reason="tool_use"),
        ],
        fallback_model="normal",
        fallback_provider="test",
    )

    assert not any(block.type == "tool_call" for block in turn.item.content)
    assert turn.stop_reason == "stop"


@pytest.mark.asyncio
async def test_engine_cancellation_during_tool_execution_preserves_completed_results() -> None:
    fast_done = asyncio.Event()
    slow_started = asyncio.Event()

    async def _fast_tool(_payload: dict[str, object]) -> ToolExecutionResult:
        fast_done.set()
        return ToolExecutionResult(status="ok", content={"tool": "fast"})

    async def _slow_tool(_payload: dict[str, object]) -> ToolExecutionResult:
        slow_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        raise AssertionError("slow tool should have been cancelled")

    engine = AgentRuntimeEngine(
        provider=_SingleToolUseProvider(),
        tool_registry=_StaticToolRegistry(
            [
                ToolDefinition(
                    name="fast_tool",
                    description="fast",
                    parameters_schema={"type": "object"},
                    execute=_fast_tool,
                ),
                ToolDefinition(
                    name="slow_tool",
                    description="slow",
                    parameters_schema={"type": "object"},
                    execute=_slow_tool,
                ),
            ]
        ),
    )

    async def _scenario() -> TurnResult:
        task = asyncio.create_task(
            engine.run_turn(
                RunTurnRequest(
                    conversation_id="conv-1",
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
    created_items = result.metadata["created_items"]
    tool_items = [item for item in created_items if item.role == "tool"]
    assert [item.role for item in created_items] == ["user", "assistant", "tool", "tool"]
    assert len(tool_items) == 2

    fast_result = next(item.content[0] for item in tool_items if item.content[0].tool_call_id == "call_fast")
    slow_result = next(item.content[0] for item in tool_items if item.content[0].tool_call_id == "call_slow")

    assert json.loads(fast_result.content)["tool"] == "fast"
    slow_payload = json.loads(slow_result.content)
    assert slow_payload["status"] == "cancelled"
    assert slow_result.metadata["cancelled_by_stop"] is True


@pytest.mark.asyncio
async def test_engine_interjection_source_appends_items_between_iterations() -> None:
    provider = _InterjectionAwareProvider()

    async def _noop_tool(_payload: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(status="ok", content={"ok": True})

    engine = AgentRuntimeEngine(
        provider=provider,
        tool_registry=_StaticToolRegistry(
            [
                ToolDefinition(
                    name="noop_tool",
                    description="noop",
                    parameters_schema={"type": "object"},
                    execute=_noop_tool,
                )
            ]
        ),
    )

    injected_once = False

    def _interjections() -> list[ConversationItem]:
        nonlocal injected_once
        if injected_once:
            return []
        injected_once = True
        return [
            ConversationItem(
                id="operator-1",
                role="user",
                content=[TextBlock(text="[Operator interjection]: continue with the new constraint")],
                metadata={"source": "operator_interjection"},
            )
        ]

    result = await engine.run_turn(
        RunTurnRequest(
            conversation_id="conv-1",
            new_items=[
                ConversationItem(
                    id="user-1",
                    role="user",
                    content=[TextBlock(text="run tools")],
                )
            ],
            config=GenerationConfig(model="normal", max_iterations=4),
            interjection_source=_interjections,
        )
    )

    assert result.status == "completed"
    assert provider.calls == 2
    second_call = provider.seen_messages[1]
    assert any(
        item.role == "user"
        and any(
            isinstance(block, TextBlock)
            and block.text == "[Operator interjection]: continue with the new constraint"
            for block in item.content
        )
        for item in second_call
    )


class _NoopProvider:
    @property
    def name(self) -> str:
        return "noop"


class _EmptyToolRegistry:
    def list_tools(self) -> list[object]:
        return []

    def get_tool(self, _name: str) -> None:
        return None


class _StaticToolRegistry:
    def __init__(self, tools: list[ToolDefinition]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def get_tool(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)


class _SingleToolUseProvider:
    @property
    def name(self) -> str:
        return "tool-use-provider"

    async def chat(self, messages, tools, config):
        raise AssertionError("chat() should not be called in this test")

    async def stream(self, messages, tools, config):
        del messages, tools, config
        yield AgentEvent(
            type="toolcall_start",
            tool_call=ToolCallBlock(id="call_fast", name="fast_tool", arguments={"label": "fast"}),
        )
        yield AgentEvent(
            type="toolcall_start",
            tool_call=ToolCallBlock(id="call_slow", name="slow_tool", arguments={"label": "slow"}),
        )
        yield AgentEvent(type="done", stop_reason="tool_use")


class _InterjectionAwareProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.seen_messages: list[list[ConversationItem]] = []

    @property
    def name(self) -> str:
        return "interjection-aware-provider"

    async def chat(self, messages, tools, config):
        raise AssertionError("chat() should not be called in this test")

    async def stream(self, messages, tools, config):
        del tools, config
        snapshot = list(messages)
        self.seen_messages.append(snapshot)
        self.calls += 1
        if self.calls == 1:
            yield AgentEvent(
                type="toolcall_start",
                tool_call=ToolCallBlock(id="call_noop", name="noop_tool", arguments={}),
            )
            yield AgentEvent(type="done", stop_reason="tool_use")
            return
        yield AgentEvent(type="done", stop_reason="stop")
