from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.sentral import ConversationItem, GenerationConfig, TextBlock, ToolCallBlock, ToolSchema
from app.services.araios.module_types import ActionDefinition, ModuleDefinition
from app.services.agent_runtime_adapters import (
    SentinelProviderAdapter,
    SentinelToolRegistryAdapter,
    runtime_item_to_sentinel_message,
    sentinel_message_to_runtime_item,
)
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import (
    AgentEvent,
    AssistantMessage,
    TextContent,
    ThinkingContent,
    TokenUsage,
    ToolCallContent,
)
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import (
    ToolApprovalEvaluation,
    ToolApprovalOutcome,
    ToolApprovalOutcomeStatus,
    ToolApprovalRequirement,
    ToolDefinition,
    ToolRegistry,
    ToolRuntimeContext,
)
from uuid import UUID


class _FakeProvider(LLMProvider):
    def __init__(self) -> None:
        self.chat_messages = None
        self.chat_tools = None
        self.chat_model = None
        self.chat_temperature = None

    @property
    def name(self) -> str:
        return "fake"

    async def chat(
        self,
        messages,
        model: str,
        tools=None,
        temperature: float = 0.7,
        reasoning_config=None,
        tool_choice=None,
    ) -> AssistantMessage:
        self.chat_messages = messages
        self.chat_tools = tools
        self.chat_model = model
        self.chat_temperature = temperature
        return AssistantMessage(
            content=[
                ThinkingContent(thinking="working"),
                TextContent(text="done"),
                ToolCallContent(id="call-1", name="notes.create", arguments={"title": "A"}),
            ],
            model="fake-model",
            provider="fake",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
        )

    async def stream(
        self,
        messages,
        model: str,
        tools=None,
        temperature: float = 0.7,
        reasoning_config=None,
        tool_choice=None,
    ) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="text_start")
        yield AgentEvent(type="text_delta", delta="hel")
        yield AgentEvent(type="text_end")
        yield AgentEvent(
            type="toolcall_end",
            tool_call=ToolCallContent(id="call-1", name="browser.navigate", arguments={"url": "https://example.com"}),
        )
        yield AgentEvent(type="done", stop_reason="tool_use")


class _FakeIndexedProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "fake-indexed"

    async def chat(
        self,
        messages,
        model: str,
        tools=None,
        temperature: float = 0.7,
        reasoning_config=None,
        tool_choice=None,
    ) -> AssistantMessage:
        raise NotImplementedError

    async def stream(
        self,
        messages,
        model: str,
        tools=None,
        temperature: float = 0.7,
        reasoning_config=None,
        tool_choice=None,
    ) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            type="toolcall_start",
            content_index=4,
            tool_call=ToolCallContent(id="call-1", name="runtime_exec", arguments={}),
        )
        yield AgentEvent(
            type="toolcall_delta",
            content_index=4,
            delta='{"command":"run_user","shell_command":"pwd"}',
        )
        yield AgentEvent(type="done", stop_reason="tool_use")


def test_runtime_and_sentinel_message_conversion_round_trip() -> None:
    item = ConversationItem(
        id="assistant-1",
        role="assistant",
        content=[
            TextBlock(text="hi"),
        ],
        metadata={
            "model": "normal",
            "provider": "openai",
            "stop_reason": "stop",
            "usage": {"input_tokens": 1, "output_tokens": 2},
        },
    )

    sentinel_message = runtime_item_to_sentinel_message(item)
    round_tripped = sentinel_message_to_runtime_item(sentinel_message, item_id="assistant-1")

    assert round_tripped.role == "assistant"
    assert round_tripped.content[0].type == "text"
    assert round_tripped.metadata["model"] == "normal"


def test_runtime_message_conversion_skips_invalid_tool_call_blocks() -> None:
    item = ConversationItem(
        id="assistant-1",
        role="assistant",
        content=[
            TextBlock(text="hi"),
            ToolCallBlock(id="", name="browser.navigate", arguments={"url": "https://example.com"}),
            ToolCallBlock(id="call-1", name="browser.navigate", arguments={"url": "https://example.com"}),
        ],
        metadata={
            "model": "normal",
            "provider": "openai",
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 1, "output_tokens": 2},
        },
    )

    sentinel_message = runtime_item_to_sentinel_message(item)

    assert isinstance(sentinel_message, AssistantMessage)
    tool_calls = [block for block in sentinel_message.content if isinstance(block, ToolCallContent)]
    assert [block.id for block in tool_calls] == ["call-1"]


@pytest.mark.asyncio
async def test_provider_adapter_converts_chat_surface() -> None:
    adapter = SentinelProviderAdapter(_FakeProvider())
    result = await adapter.chat(
        messages=[
            ConversationItem(
                id="user-1",
                role="user",
                content=[TextBlock(text="hello")],
            )
        ],
        tools=[ToolSchema(name="notes.create", description="Create note", parameters={"type": "object"})],
        config=GenerationConfig(model="normal", temperature=0.2),
    )

    assert result.item.role == "assistant"
    assert result.item.metadata["provider"] == "fake"
    assert result.item.metadata["stop_reason"] == "tool_use"
    assert result.usage.output_tokens == 5


@pytest.mark.asyncio
async def test_provider_adapter_converts_stream_events() -> None:
    adapter = SentinelProviderAdapter(_FakeProvider())
    events = []
    async for event in adapter.stream(
        messages=[],
        tools=[],
        config=GenerationConfig(model="normal"),
    ):
        events.append(event)

    assert [event.type for event in events] == [
        "text_start",
        "text_delta",
        "text_end",
        "toolcall_end",
        "done",
    ]
    assert events[3].tool_call is not None
    assert events[3].tool_call.name == "browser.navigate"


@pytest.mark.asyncio
async def test_provider_adapter_preserves_content_index_for_streamed_tool_calls() -> None:
    adapter = SentinelProviderAdapter(_FakeIndexedProvider())
    events = []
    async for event in adapter.stream(
        messages=[],
        tools=[],
        config=GenerationConfig(model="normal"),
    ):
        events.append(event)

    tool_start = next(event for event in events if event.type == "toolcall_start")
    tool_delta = next(event for event in events if event.type == "toolcall_delta")

    assert tool_start.metadata["content_index"] == 4
    assert tool_delta.metadata["content_index"] == 4
    assert tool_delta.delta == '{"command":"run_user","shell_command":"pwd"}'


@pytest.mark.asyncio
async def test_tool_registry_adapter_maps_ok_result() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="notes.create",
            description="Create a note",
            parameters_schema={"type": "object", "properties": {"title": {"type": "string"}}},
            execute=_ok_tool,
        )
    )
    adapter = SentinelToolRegistryAdapter(registry, ToolExecutor(registry))

    tool = adapter.get_tool("notes.create")
    assert tool is not None
    result = await tool.execute({"title": "Test"})

    assert result.status == "ok"
    assert result.content == {"ok": True, "title": "Test"}


@pytest.mark.asyncio
async def test_tool_registry_adapter_maps_pending_approval() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="git.push",
            description="Push branch",
            parameters_schema={"type": "object", "properties": {}, "additionalProperties": True},
            execute=_ok_tool,
            approval_check=lambda: ToolApprovalEvaluation.require(
                ToolApprovalRequirement(
                    action="git.push",
                    description="Push current branch to remote.",
                )
            ),
        )
    )
    executor = ToolExecutor(registry, approval_waiter=_fake_pending_approval_waiter)
    adapter = SentinelToolRegistryAdapter(registry, executor)

    tool = adapter.get_tool("git.push")
    assert tool is not None
    result = await tool.execute({})

    assert result.status == "pending_approval"
    assert result.approval_request is not None
    assert result.approval_request.id == "approval-1"
    assert result.approval_request.action == "git.push"


@pytest.mark.asyncio
async def test_tool_registry_adapter_hides_and_injects_session_id_for_grouped_tools() -> None:
    session_id = "00000000-0000-0000-0000-000000000123"
    seen_payloads: list[tuple[dict[str, Any], ToolRuntimeContext]] = []

    async def _handle_run_user(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
        seen_payloads.append((dict(payload), runtime))
        return {"ok": True, "payload": dict(payload)}

    module = ModuleDefinition(
        name="runtime_exec",
        label="Runtime Exec",
        grouped_tool=True,
        actions=[
            ActionDefinition(
                id="run_user",
                label="Run User",
                handler=_handle_run_user,
                parameters_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["shell_command"],
                    "properties": {
                        "shell_command": {"type": "string"},
                    },
                },
                requires_runtime_context=True,
            ),
        ],
    )
    registry = ToolRegistry()
    registry.register(module.to_tool_definitions()[0])
    adapter = SentinelToolRegistryAdapter(
        registry,
        ToolExecutor(registry),
        session_id=session_id,
    )

    tool = adapter.get_tool("runtime_exec")
    assert tool is not None
    assert "command" in tool.parameters_schema["required"]

    result = await tool.execute({"command": "run_user", "shell_command": "pwd"})

    assert result.status == "ok"
    assert seen_payloads == [
        ({"shell_command": "pwd"}, ToolRuntimeContext(session_id=UUID(session_id)))
    ]


async def _ok_tool(payload: dict[str, Any], runtime: ToolRuntimeContext) -> dict[str, Any]:
    _ = runtime
    return {"ok": True, **payload}


async def _fake_pending_approval_waiter(
    tool_name: str,
    payload: dict[str, Any],
    runtime: ToolRuntimeContext,
    requirement: ToolApprovalRequirement,
    on_pending_approval: Any = None,
) -> ToolApprovalOutcome:
    _ = runtime
    pending_payload = {
        "provider": tool_name,
        "approval_id": "approval-1",
        "status": "pending",
        "pending": True,
        "can_resolve": True,
        "label": f"{tool_name} approval",
        "action": requirement.action,
        "description": requirement.description,
    }
    if callable(on_pending_approval):
        await on_pending_approval(pending_payload)
    return ToolApprovalOutcome(
        status=ToolApprovalOutcomeStatus.CANCELLED,
        approval=pending_payload,
        message="Approval cancelled.",
    )
