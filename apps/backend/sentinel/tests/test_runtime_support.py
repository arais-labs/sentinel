from __future__ import annotations

import asyncio
import json

from app.models import Message, Session
from app.services.agent import SentinelRuntimeSupport
from app.services.agent.runtime_support import humanize_error
from app.services.llm.generic.types import (
    AssistantMessage,
    ImageContent,
    SystemMessage,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _new_session(db: FakeDB, user_id: str = "dev-admin") -> Session:
    session = Session(user_id=user_id, status="active", title="test")
    db.add(session)
    return session


def _support() -> SentinelRuntimeSupport:
    return SentinelRuntimeSupport(provider=None, context_builder=None, tool_adapter=None)


def test_runtime_context_snapshot_includes_conversation_history_layer():
    messages = [
        SystemMessage(
            content="System prompt",
            metadata={"layer": "core", "kind": "base_prompt", "title": "Core Prompt"},
        ),
        UserMessage(
            content=[TextContent(text="Need the latest status"), ImageContent(data="abc")],
            metadata={"source": "web"},
        ),
        AssistantMessage(
            content=[
                TextContent(text="I will check the repository."),
                ToolCallContent(
                    id="call_1",
                    name="git",
                    arguments={"command": "read", "cli_command": "git status"},
                ),
            ]
        ),
        ToolResultMessage(
            tool_call_id="call_1",
            tool_name="git",
            content='{"ok": true}',
            is_error=False,
        ),
    ]

    snapshot = _support().build_runtime_context_snapshot(
        messages,
        [],
        model="normal",
        temperature=0.7,
        max_iterations=50,
        stream=True,
        agent_mode="normal",
    )
    structured = snapshot["structured_context"]
    layers = structured["layers"]
    history_layer = next(layer for layer in layers if layer.get("kind") == "conversation_history")

    assert history_layer["layer"] == "history"
    assert history_layer["title"] == "Injected Previous Messages"

    history_messages = history_layer["history_messages"]
    assert len(history_messages) == 3
    assert history_messages[0]["role"] == "user"
    assert history_messages[0]["source"] == "web"
    assert history_messages[0]["image_count"] == 1
    assert history_messages[1]["role"] == "assistant"
    assert history_messages[1]["tool_call_count"] == 1
    assert history_messages[1]["tool_calls"][0]["name"] == "git"
    assert history_messages[2]["role"] == "tool_result"
    assert history_messages[2]["tool_name"] == "git"
    assert structured["history_message_count"] == 3


def test_humanize_error_preserves_provider_diagnostics():
    raw = (
        "All providers failed. "
        "anthropic:gpt-5: authentication_error invalid_api_key | "
        "openai:gpt-5-mini: rate_limit_exceeded"
    )
    humanized = humanize_error(raw)

    assert humanized.startswith("All AI providers failed.")
    assert "anthropic:gpt-5: authentication_error invalid_api_key" in humanized
    assert "openai:gpt-5-mini: rate_limit_exceeded" in humanized


def test_humanize_error_truncates_long_provider_diagnostics():
    raw = "All providers failed. " + ("x" * 2000)
    humanized = humanize_error(raw)

    assert humanized.startswith("All AI providers failed.")
    assert len(humanized) == 700


def test_runtime_support_persists_distinct_created_at_order_and_iteration_metadata():
    db = FakeDB()
    session = _new_session(db)
    support = _support()
    created = [
        UserMessage(content=[TextContent(text="check ordering")]),
        AssistantMessage(
            content=[ToolCallContent(id="c1", name="lookup", arguments={"query": "x"})],
            model="m",
            provider="p",
        ),
        ToolResultMessage(tool_call_id="c1", tool_name="lookup", content='{"value":"x"}'),
        AssistantMessage(
            content=[TextContent(text="done")],
            model="m",
            provider="p",
        ),
    ]
    assistant_iterations = {
        id(created[1]): 1,
        id(created[3]): 2,
    }

    _run(
        support._persist_messages(
            db,
            session.id,
            created,
            assistant_iterations,
            requested_tier="normal",
            temperature=0.7,
            max_iterations=50,
        )
    )

    saved = [m for m in db.storage[Message] if m.session_id == session.id]
    created_ats = [item.created_at for item in saved]
    assert all(created_ats[idx] < created_ats[idx + 1] for idx in range(len(created_ats) - 1))
    assert len(set(created_ats)) == len(created_ats)

    assistants = [item for item in saved if item.role == "assistant"]
    assert assistants[0].metadata_json.get("iteration") == 1
    assert assistants[1].metadata_json.get("iteration") == 2


def test_runtime_support_truncates_large_tool_results_for_storage():
    db = FakeDB()
    session = _new_session(db)
    support = _support()
    created = [
        ToolResultMessage(
            tool_call_id="call_big",
            tool_name="big",
            content=json.dumps({"blob": "x" * 60000}),
            is_error=False,
        )
    ]

    _run(
        support._persist_messages(
            db,
            session.id,
            created,
            {},
            requested_tier="normal",
            temperature=0.7,
            max_iterations=50,
        )
    )

    [tool_record] = [m for m in db.storage[Message] if m.session_id == session.id]
    assert (
        "[TRUNCATED - " in tool_record.content
        or "[TRUNCATED_FOR_STORAGE - " in tool_record.content
    )
    assert tool_record.metadata_json.get("storage_truncated") is True
    assert int(tool_record.metadata_json.get("original_chars") or 0) > len(tool_record.content)
