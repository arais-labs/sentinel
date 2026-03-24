from __future__ import annotations

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


class _NoopProvider:
    @property
    def name(self) -> str:
        return "noop"


class _EmptyToolRegistry:
    def list_tools(self) -> list[object]:
        return []

    def get_tool(self, _name: str) -> None:
        return None
