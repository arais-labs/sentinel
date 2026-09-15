from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.models import Message
from sentral import (
    AgentEvent,
    AgentRuntimeEngine,
    ConversationItem,
    GenerationConfig,
    RunTurnRequest,
    TextBlock,
)
from sentral.types import AssistantTurn
from app.services.agent.context_builder import ContextBuilder
from app.services.agent.runtime_support import SentinelRuntimeSupport
from app.services.agent_runtime_adapters.runtime import SentinelLoopRuntimeAdapter
from app.services.agent_runtime_adapters.conversions import db_messages_to_runtime_items
from sentral.llm.generic.types import AssistantMessage, TextContent, UserMessage
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.tools import ToolRegistry, ToolExecutor
from tests.fake_db import FakeDB
from tests.helpers import install_fake_db_overrides, make_fake_instance_context, restore_test_app
from tests.test_agent_runtime_contracts import _EmptyToolRegistry
from tests.test_agent_runtime_adapters import _FakeProvider
from tests.test_runtime_support import _new_session


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_steering_arriving_during_final_response_is_consumed_in_same_run(stream):
    queued, seen = [], []

    class Provider:
        name = "test"

        async def chat(self, *, messages, **kwargs):
            seen.append(deepcopy(messages))
            if len(seen) == 1:
                queued.append(
                    ConversationItem(
                        id="steer", role="user", content=[TextBlock(text="Only the header")]
                    )
                )
            return AssistantTurn(
                item=ConversationItem(
                    id=f"a{len(seen)}", role="assistant", content=[TextBlock(text="Done")]
                ),
                stop_reason="stop",
            )

        async def stream(self, **kwargs):
            await self.chat(**kwargs)
            yield AgentEvent(type="text_delta", delta="Done")
            yield AgentEvent(type="done", stop_reason="stop")

    def drain():
        items = list(queued)
        queued.clear()
        return items

    result = await AgentRuntimeEngine(
        provider=Provider(), tool_registry=_EmptyToolRegistry()
    ).run_turn(
        RunTurnRequest(
            new_items=[
                ConversationItem(id="u", role="user", content=[TextBlock(text="Update UI")])
            ],
            config=GenerationConfig(model="normal", stream=stream),
            interjection_source=drain,
        )
    )
    assert len(seen) == 2
    assert [item.id for item in seen[1]].count("steer") == 1
    assert result.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("incremental", [False, True])
async def test_persisted_and_queued_steering_is_delivered_once_without_duplicate_message(
    incremental,
):
    db = FakeDB()
    session = _new_session(db)
    message = Message(id=uuid4(), session_id=session.id, role="user", content="Only the header")
    message.metadata_json = {"steering": "pending", "steering_id": str(message.id)}
    db.add(message)
    await db.commit()
    queued, seen = db_messages_to_runtime_items([message]), []

    class Context:
        async def build(self, *args, **kwargs):
            return [
                ContextBuilder()._convert_message_with_options(message, include_tool_calls=True)
            ]

    class Provider(_FakeProvider):
        async def chat(self, messages, **kwargs):
            seen.extend(messages)
            return AssistantMessage(
                content=[TextContent(text="Done")],
                stop_reason="stop",
                model="test",
                provider="test",
            )

    def drain():
        items = list(queued)
        queued.clear()
        return items

    tools = ToolRegistry()
    support = SentinelRuntimeSupport(
        provider=Provider(),
        context_builder=Context(),
        tool_registry=tools,
        tool_executor=ToolExecutor(tools),
    )
    adapter = SentinelLoopRuntimeAdapter(
        loop=support, db=db, session_id=session.id, persist_incremental=incremental
    )
    events = []

    async def sink(event):
        events.append(event)

    await adapter.run_turn(
        RunTurnRequest(
            conversation_id=str(session.id),
            new_items=[],
            interjection_source=drain,
            config=GenerationConfig(
                model="normal", stream=False, provider_metadata={"persist_user_message": False}
            ),
        ),
        sink=sink,
    )
    assert (
        len([m for m in seen if isinstance(m, UserMessage) and m.content == "Only the header"]) == 1
    )
    assert len([m for m in db.storage[Message] if m.role == "user"]) == 1
    assert message.metadata_json["steering"] == "delivered"
    assert len([event for event in events if event.type == "steering_delivered"]) == 1


@pytest.mark.asyncio
async def test_idle_steering_notifies_after_run_clear_and_shutdown_does_not_resume():
    registry, notifications = AgentRunRegistry(), []

    async def notify(key):
        notifications.append(key)

    registry.configure_idle_interjections_callback(notify)
    task = asyncio.create_task(asyncio.sleep(0))
    await registry.register("s", task)
    registry.enqueue_interjection(
        "s", ConversationItem(id="u", role="user", metadata={"steering": "pending"})
    )
    await registry.notify_idle_interjections("s")
    assert not notifications
    await task
    await registry.clear("s", task)
    assert notifications == ["s"]
    await registry.cancel_all()
    await registry.notify_idle_interjections("s")
    assert notifications == ["s"]


def test_steering_endpoint_persists_and_deduplicates():
    db = FakeDB()
    support = SimpleNamespace(provider=SimpleNamespace(model_context=lambda model: {}))
    context = make_fake_instance_context(app_db=db, agent_runtime_support=support)
    old_init = install_fake_db_overrides(app_db=db, instance_context=context)
    old_registry = getattr(app.state, "agent_run_registry", None)
    registry = AgentRunRegistry()
    app.state.agent_run_registry = registry
    base = "/api/v1/instances/main/sessions"
    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        session = client.post(base, json={"title": "Steering"}).json()
        payload = {"message_id": str(uuid4()), "content": "Only the header", "max_iterations": 0}
        first = client.post(f"{base}/{session['id']}/steer", json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["metadata"]["steering"] == "pending"
        second = client.post(f"{base}/{session['id']}/steer", json=payload)
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        assert len(registry.peek_interjections(session["id"])) == 1
        assert len([m for m in db.storage[Message] if m.content == "Only the header"]) == 1
        bad = client.post(
            f"{base}/{session['id']}/steer",
            json={
                **payload,
                "message_id": str(uuid4()),
                "attachments": [{"mime_type": "image/png", "base64": "bad!"}],
            },
        )
        assert bad.status_code == 422
        stopped = client.post(f"{base}/{session['id']}/stop")
        assert stopped.status_code == 200
        assert registry.peek_interjections(session["id"]) == []
        assert (
            next(m for m in db.storage[Message] if m.content == "Only the header").metadata_json[
                "steering"
            ]
            == "cancelled"
        )
    finally:
        app.state.agent_run_registry = old_registry
        restore_test_app(old_init)


def test_reloaded_steering_keeps_tool_calls_and_results_adjacent():
    from app.services.sessions.history import order_steering_history

    call = Message(
        id=uuid4(),
        role="assistant",
        content="Checking",
        metadata_json={"tool_calls": [{"id": "c1", "name": "read", "arguments": {}}]},
    )
    result = Message(id=uuid4(), role="tool", tool_call_id="c1", tool_name="read", content="OK")
    update = Message(
        id=uuid4(),
        role="user",
        content="Only the header",
        metadata_json={"steering": "delivered", "steering_after_message_id": str(result.id)},
    )
    # This is the order of arrival shown in the UI.
    rows = [call, update, result]
    assert order_steering_history(rows) == [call, result, update]
    restored = db_messages_to_runtime_items(rows)
    assert [item.role for item in restored] == ["assistant", "tool", "user"]
    assert restored[0].content[-1].id == "c1"


@pytest.mark.asyncio
async def test_wakeup_does_not_start_after_another_run_consumes_steering():
    registry = AgentRunRegistry()
    ran = []

    async def run():
        ran.append(True)

    assert await registry.start("s", run(), require_interjections=True) is None
    assert not ran


@pytest.mark.parametrize(
    "provider_kind",
    [
        "openai",
        "openai-responses",
        "codex",
        "anthropic",
        "anthropic-oauth",
        "gemini",
        "gemini-oauth",
    ],
)
def test_provider_codecs_preserve_steering_after_tool_results(provider_kind):
    import json
    from sentral.llm.providers.openai import OpenAIProvider
    from sentral.llm.providers.codex import CodexProvider
    from sentral.llm.providers.anthropic import AnthropicProvider
    from sentral.llm.providers.gemini import GeminiProvider
    from sentral.llm.providers.gemini_oauth import GeminiOAuthProvider, GeminiOAuthCredentials
    from sentral.llm.generic.types import ToolCallContent, ToolResultMessage

    history = [
        UserMessage(content="Update the UI"),
        AssistantMessage(content=[ToolCallContent(id="c1", name="read", arguments={})]),
        ToolResultMessage(tool_call_id="c1", tool_name="read", content="FILE CONTENT"),
        UserMessage(content="ONLY THE HEADER", metadata={"steering_id": "s1"}),
    ]
    if provider_kind in {"openai", "openai-responses", "codex"}:
        provider = CodexProvider("test") if provider_kind == "codex" else OpenAIProvider("test")
        encoded = (
            provider._to_openai_messages(history)
            if provider_kind == "openai"
            else provider._to_responses_input(history)[1]
        )
    elif provider_kind.startswith("anthropic"):
        provider = AnthropicProvider(
            "sk-ant-oat-test" if provider_kind.endswith("oauth") else "test"
        )
        encoded = provider._to_anthropic_messages(history)
    else:
        provider = (
            GeminiOAuthProvider(GeminiOAuthCredentials(access_token="test"))
            if provider_kind.endswith("oauth")
            else GeminiProvider("test")
        )
        encoded = provider._to_gemini_contents(history)[1]
    wire = json.dumps(encoded)
    assert wire.count("ONLY THE HEADER") == 1
    assert wire.index("FILE CONTENT") < wire.index("ONLY THE HEADER")


@pytest.mark.asyncio
async def test_pending_steering_survives_compacted_history_boundary():
    from datetime import UTC, datetime, timedelta
    from app.models import SessionSummary
    from app.services.sessions.history import context_history

    db = FakeDB()
    session = _new_session(db)
    sent_at = datetime.now(UTC)
    pending = Message(
        id=uuid4(),
        session_id=session.id,
        role="user",
        content="Only the header",
        created_at=sent_at,
        metadata_json={"steering": "pending"},
    )
    boundary = Message(
        id=uuid4(),
        session_id=session.id,
        role="assistant",
        content="Previous work",
        created_at=sent_at + timedelta(seconds=1),
    )
    db.add(pending)
    db.add(boundary)
    db.add(SessionSummary(session_id=session.id, summary={"through_message_id": str(boundary.id)}))
    _, history = await context_history(db, session.id)
    assert pending in history
    assert boundary not in history
