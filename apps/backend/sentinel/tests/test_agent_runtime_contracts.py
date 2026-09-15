from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import assert_type

import pytest

from app.models import Message
from sentral import (
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
from app.services.agent.context_builder import ContextBuilder
from sentral.llm.runtime_conversions import (
    runtime_item_to_sentinel_message,
    sentinel_event_to_runtime_event,
    sentinel_message_to_runtime_item,
)
from app.services.agent_runtime_adapters.conversions import db_messages_to_runtime_items
from sentral.llm.generic.types import (
    AgentEvent as ProviderAgentEvent,
)
from sentral.llm.generic.types import (
    AssistantMessage,
    TextContent,
    TokenUsage,
    ToolCallContent,
    ToolResultMessage,
)
from app.services.modules.tool_adapter import build_module_tools
from tests.fake_db import FakeDB
from tests.test_runtime_support import _new_session, _support


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
                tool_call=ToolCallBlock(id="", name="runtime", arguments={"action": "pwd"}),
            ),
            AgentEvent(type="toolcall_delta", delta='{"action":"pwd"}'),
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
    assert [item.role for item in created_items] == [
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    assert len(tool_items) == 2

    fast_result = next(
        item.content[0] for item in tool_items if item.content[0].tool_call_id == "call_fast"
    )
    slow_result = next(
        item.content[0] for item in tool_items if item.content[0].tool_call_id == "call_slow"
    )

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
                content=[
                    TextBlock(text="[Operator interjection]: continue with the new constraint")
                ],
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


"""Responses opaque items survive assembly, tool turns, and persisted history."""


def _output():
    return [
        {
            "id": "rs_test",
            "type": "reasoning",
            "summary": [],
            "encrypted_content": "opaque-test",
        },
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "Checking"}],
        },
        {
            "id": "fc_test",
            "type": "function_call",
            "call_id": "c1",
            "name": "echo",
            "arguments": "{}",
        },
    ]


@pytest.mark.parametrize("reset", [False, True])
def test_responses_output_survives_stream_assembly_and_conversion(reset):
    output = _output()
    final = AssistantMessage(
        provider="codex",
        model="gpt-5.6-sol",
        responses_output=output,
        responses_context_reset=reset,
        usage=TokenUsage(input_tokens=119, output_tokens=5),
        provider_usage={"usage": {"input_tokens": 119, "output_tokens": 5}},
    )
    events = [
        ProviderAgentEvent(type="text_delta", content_index=1, delta="Checking"),
        ProviderAgentEvent(
            type="toolcall_start",
            content_index=2,
            tool_call=ToolCallContent(id="c1", name="echo"),
        ),
        ProviderAgentEvent(type="done", stop_reason="tool_use", message=final),
        ProviderAgentEvent(type="done", stop_reason="tool_use", message=final),
    ]
    engine = AgentRuntimeEngine(provider=None, tool_registry=None)
    turn = engine._assemble_turn_from_events(
        [sentinel_event_to_runtime_event(event) for event in events],
        fallback_model="normal",
        fallback_provider="fallback",
    )
    restored = runtime_item_to_sentinel_message(turn.item)
    assert isinstance(restored, AssistantMessage)
    assert restored.responses_context_reset is reset
    assert restored.responses_output == output
    assert restored.provider_usage == final.provider_usage
    assert restored.usage.input_tokens == 119
    assert restored.usage.output_tokens == 5
    assert restored.provider == "codex"
    assert restored.content[0].text == "Checking"
    final.responses_output[0]["encrypted_content"] = "changed"
    assert restored.responses_output[0]["encrypted_content"] == "opaque-test"


@pytest.mark.parametrize("reset", [False, True])
def test_responses_output_persists_with_provider_and_reloads_both_history_paths(reset):
    db = FakeDB()
    session = _new_session(db)
    output = _output()
    assistant = AssistantMessage(
        content=[TextContent(text="Checking"), ToolCallContent(id="c1", name="echo")],
        model="gpt-5.6-sol",
        provider="codex",
        responses_output=deepcopy(output),
        responses_context_reset=reset,
        provider_usage={"usage": {"input_tokens": 119, "output_tokens": 5}},
    )
    result = ToolResultMessage(tool_call_id="c1", tool_name="echo", content="ok")
    asyncio.run(
        _support()._persist_messages(
            db,
            session.id,
            [assistant, result],
            {},
            requested_tier="normal",
            temperature=0.7,
            max_iterations=8,
        )
    )
    rows = [row for row in db.storage[Message] if row.session_id == session.id]
    assert rows[0].metadata_json["responses_output"] == output
    assert rows[0].metadata_json["provider_usage"] == assistant.provider_usage
    runtime = db_messages_to_runtime_items(rows)
    restored = runtime_item_to_sentinel_message(runtime[0])
    assert restored.responses_context_reset is reset
    assert restored.responses_output == output
    assert restored.provider == "codex"
    legacy = ContextBuilder()._convert_message_with_options(rows[0], include_tool_calls=True)
    assert restored.provider_usage == assistant.provider_usage
    assert legacy.provider_usage == assistant.provider_usage
    assert legacy.responses_context_reset is reset
    assert legacy.responses_output == output
    assert legacy.provider == "codex"
    orphan = runtime_item_to_sentinel_message(db_messages_to_runtime_items(rows[:1])[0])
    assert orphan.responses_output == []
    assert (
        ContextBuilder()
        ._convert_message_with_options(rows[0], include_tool_calls=False)
        .responses_output
        == []
    )


def test_responses_output_final_reply_defaults_and_defensive_copy():
    assert AssistantMessage().responses_output == []
    original = AssistantMessage(provider="openai", responses_output=_output()[:2])
    item = sentinel_message_to_runtime_item(original, item_id="assistant-test")
    original.responses_output.clear()
    restored = runtime_item_to_sentinel_message(item)
    assert len(restored.responses_output) == 2
    assert restored.provider == "openai"


@pytest.mark.asyncio
async def test_form_pauses_turn_after_persisting_tool_result() -> None:
    from app.services.modules.builtins.form.module import MODULE

    class FormProvider:
        name = "form-test"
        calls = 0

        async def stream(self, messages, tools, config):
            self.calls += 1
            assert self.calls == 1, "The agent must wait for user input"
            yield AgentEvent(
                type="toolcall_start",
                tool_call=ToolCallBlock(
                    id="form-call",
                    name="form",
                    arguments={
                        "action": "present",
                        "title": "Direction",
                        "questions": [{"id": "1", "question": "What next?"}],
                    },
                ),
            )
            yield AgentEvent(type="done", stop_reason="tool_use")

    async def execute(payload):
        tool = build_module_tools(
            MODULE,
        )[0]
        return ToolExecutionResult(status="ok", content=await tool.execute(payload, None))

    provider = FormProvider()
    engine = AgentRuntimeEngine(
        provider=provider,
        tool_registry=_StaticToolRegistry(
            [
                ToolDefinition(
                    name="form",
                    description="Form",
                    parameters_schema=build_module_tools(
                        MODULE,
                    )[0].parameters_schema,
                    execute=execute,
                )
            ]
        ),
    )
    result = await engine.run_turn(
        RunTurnRequest(
            conversation_id="form-session",
            new_items=[
                ConversationItem(id="user-form", role="user", content=[TextBlock(text="Ask me")])
            ],
            config=GenerationConfig(model="normal", stream=True, max_iterations=4),
        )
    )
    assert result.stop_reason == "awaiting_input"
    assert provider.calls == 1
    assert any(item.role == "tool" for item in result.metadata["created_items"])


@pytest.mark.asyncio
async def test_auto_steps_continue_until_provider_finishes() -> None:
    from sentral.types import AssistantTurn

    class Provider:
        name = "test"
        calls = 0

        async def chat(self, *, messages, tools, config):
            self.calls += 1
            assert config.tool_choice != "none"
            return AssistantTurn(
                item=ConversationItem(
                    id=f"assistant-{self.calls}",
                    role="assistant",
                    content=[TextBlock(text="Working" if self.calls < 70 else "Done")],
                ),
                stop_reason="pause_turn" if self.calls < 70 else "stop",
            )

    provider = Provider()
    engine = AgentRuntimeEngine(provider=provider, tool_registry=_EmptyToolRegistry())
    await engine.run_turn(
        RunTurnRequest(
            conversation_id="auto-steps",
            new_items=[
                ConversationItem(id="user-auto", role="user", content=[TextBlock(text="Go")])
            ],
            config=GenerationConfig(model="normal", stream=False, max_iterations=0),
        )
    )
    assert provider.calls == 70
