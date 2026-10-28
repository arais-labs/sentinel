"""A tool result is cut once, when the tool returns; the history replays exactly those bytes."""

from __future__ import annotations

import json
from uuid import uuid4

from app.models import Message
from app.services.agent.context_builder import ContextBuilder
from sentral.engine import AgentRuntimeEngine
from sentral.types import ToolCallBlock, ToolExecutionResult


def _engine(cap):
    return AgentRuntimeEngine(provider=None, tool_registry=None, tool_result_max_chars=cap)


def _block(engine, result):
    return engine._tool_execution_to_result_block(
        ToolCallBlock(id="call_1", name="t", arguments={}), result
    )


def test_result_under_the_cap_is_untouched():
    content = {"text": "x" * 900}
    block = _block(_engine(1000), ToolExecutionResult(status="ok", content=content))
    assert block.content == json.dumps(content)
    assert "output_cut" not in block.metadata


def test_result_over_the_cap_keeps_start_and_end_with_a_marker():
    content = "A" * 30 + "B" * 100 + "C" * 30
    block = _block(_engine(60), ToolExecutionResult(status="ok", content=content))
    assert block.content.startswith("A" * 30)
    assert block.content.endswith("C" * 30)
    assert "output cut: 60 of 160 characters shown, 100 removed here" in block.content
    assert block.metadata["output_cut"] == {"shown_chars": 60, "original_chars": 160}


def test_errors_are_capped_too():
    block = _block(_engine(10), ToolExecutionResult(status="error", error="e" * 50))
    assert block.is_error and "output cut: 10 of 50" in block.content


def test_no_cap_configured_keeps_everything():
    block = _block(_engine(None), ToolExecutionResult(status="ok", content="z" * 100_000))
    assert block.content == "z" * 100_000


def test_history_replays_a_stored_result_byte_for_byte():
    content = json.dumps({"text": "guide " * 5000})  # well over the old 4,000 and 500-char cuts
    row = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="tool_result",
        content=content,
        metadata_json={"is_error": False},
        tool_call_id="call_1",
        tool_name="workspaces",
    )
    replayed = ContextBuilder()._convert_message_with_options(row, include_tool_calls=True)
    assert replayed.content == content
