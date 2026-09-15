import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient


from app.main import app
from app.models import Message, Session, SessionSummary
from app.services.sessions.compaction import CompactionService
from sentral.llm.generic.base import LLMProvider
from sentral.llm.generic.types import AgentEvent, AssistantMessage, TextContent
from tests.fake_db import FakeDB
from tests.helpers import (
    install_fake_db_overrides,
    make_fake_instance_context,
    restore_test_app,
)


def _run(coro):
    return asyncio.run(coro)


class _NoopProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "noop"

    async def chat(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
        return AssistantMessage(
            content=[TextContent(text='{"context_summary":"Summary of earlier turns"}')],
            model=model,
            provider=self.name,
        )

    async def stream(
        self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None
    ):
        yield AgentEvent(type="start")
        yield AgentEvent(type="done", stop_reason="stop")


def test_compaction_create_idempotent():
    fake_db = FakeDB()

    fake_context = make_fake_instance_context(
        app_db=fake_db,
        agent_runtime_support=SimpleNamespace(provider=_NoopProvider()),
    )
    old_init = install_fake_db_overrides(app_db=fake_db, instance_context=fake_context)

    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )

        owner_headers = {"x-sentinel-desktop-token": "test-desktop-transport-token"}

        session = client.post(
            "/api/v1/instances/main/sessions", json={"title": "compact-me"}, headers=owner_headers
        )
        assert session.status_code == 200
        session_id = session.json()["id"]

        for i in range(15):
            content = f"message {i} " + " ".join(["longtext"] * 24)
            role = "user" if i % 2 == 0 else "system"
            posted = client.post(
                f"/api/v1/instances/main/sessions/{session_id}/messages",
                json={"role": role, "content": content, "metadata": {}},
                headers=owner_headers,
            )
            assert posted.status_code == 200

        compacted = client.post(
            f"/api/v1/instances/main/sessions/{session_id}/compact", headers=owner_headers
        )
        assert compacted.status_code == 200
        payload = compacted.json()
        assert payload["session_id"] == session_id
        assert payload["compacted"] is True

        summaries = fake_db.storage[SessionSummary]
        assert len(summaries) == 1
        first_summary_id = summaries[0].id

        compacted_again = client.post(
            f"/api/v1/instances/main/sessions/{session_id}/compact", headers=owner_headers
        )
        assert compacted_again.status_code == 200
        second_payload = compacted_again.json()
        assert second_payload["compacted"] is False
        summaries_after = fake_db.storage[SessionSummary]
        assert len(summaries_after) == 1
        assert summaries_after[0].id == first_summary_id

    finally:
        restore_test_app(old_init)


def test_compaction_noop_when_context_is_small():
    fake_db = FakeDB()

    fake_context = make_fake_instance_context(
        app_db=fake_db,
        agent_runtime_support=SimpleNamespace(provider=_NoopProvider()),
    )
    old_init = install_fake_db_overrides(app_db=fake_db, instance_context=fake_context)

    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        headers = {"x-sentinel-desktop-token": "test-desktop-transport-token"}

        session = client.post(
            "/api/v1/instances/main/sessions", json={"title": "small-context"}, headers=headers
        )
        session_id = session.json()["id"]

        for i in range(5):
            posted = client.post(
                f"/api/v1/instances/main/sessions/{session_id}/messages",
                json={"role": "user", "content": f"short {i}", "metadata": {}},
                headers=headers,
            )
            assert posted.status_code == 200

        compacted = client.post(
            f"/api/v1/instances/main/sessions/{session_id}/compact", headers=headers
        )
        assert compacted.status_code == 200
        payload = compacted.json()
        assert payload["compacted"] is False
    finally:
        restore_test_app(old_init)


def test_compaction_retains_coherent_recent_turn_not_just_last_10_rows():
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="turn-aware-retain")
    fake_db.add(session)

    # Older turn to compact away
    fake_db.add(Message(session_id=session.id, role="user", content="old-user-1", metadata_json={}))
    fake_db.add(
        Message(
            session_id=session.id, role="assistant", content="old-assistant-1", metadata_json={}
        )
    )

    # Most recent turn: one user + many tool results + final assistant.
    # Legacy row-count retention would drop this user row when trimming to last 10 rows.
    fake_db.add(
        Message(session_id=session.id, role="user", content="latest-user-turn", metadata_json={})
    )
    for idx in range(15):
        fake_db.add(
            Message(
                session_id=session.id,
                role="tool_result",
                content=f'{{"status":"ok","idx":{idx}}}',
                metadata_json={"is_error": False},
                tool_call_id=f"tool_{idx}",
                tool_name="module_manager",
            )
        )
    fake_db.add(
        Message(
            session_id=session.id,
            role="assistant",
            content="latest assistant answer",
            metadata_json={"stop_reason": "stop"},
        )
    )

    service = CompactionService(provider=None)
    result = _run(service.compact_session(fake_db, session_id=session.id, user_id="dev-admin"))
    assert result.compacted is True

    kept = [m for m in fake_db.storage[Message] if m.session_id == session.id]
    kept.sort(key=lambda m: m.created_at)
    assert len(kept) > 10
    assert any(m.role == "user" and m.content == "latest-user-turn" for m in kept)
    assert kept[0].role == "user"
    assert kept[0].content == "old-user-1"
    from app.services.sessions.history import context_history

    _, active = _run(context_history(fake_db, session.id))
    assert active[0].content == "latest-user-turn"

    summaries = [s for s in fake_db.storage[SessionSummary] if s.session_id == session.id]
    assert len(summaries) == 1
    summary_payload = summaries[0].summary or {}
    assert summary_payload.get("active_message_count") == len(active)
    assert summary_payload.get("compacted_message_count") == 2


def test_compaction_preserves_full_history_and_prior_summary_and_failure_boundary():
    import copy
    import pytest
    from app.services.sessions.history import context_history

    class Summarizer(_NoopProvider):
        prompts = []
        fail = False

        async def chat(self, messages, model, **kwargs):
            self.prompts.append(messages[-1]["content"])
            if self.fail:
                return AssistantMessage(
                    content=[TextContent(text="invalid")], model=model, provider=self.name
                )
            return AssistantMessage(
                content=[TextContent(text='{"context_summary":"' + "retained detail " * 30 + '"}')],
                model=model,
                provider=self.name,
            )

    db = FakeDB()
    session = Session(user_id="local", status="active", title="History")
    db.add(session)

    def add_turns(count):
        for i in range(count):
            db.add(
                Message(
                    session_id=session.id,
                    role="user",
                    content="prefix " * 30 + "IMPORTANT_END",
                    metadata_json={},
                )
            )
            db.add(
                Message(
                    session_id=session.id, role="assistant", content=f"answer {i}", metadata_json={}
                )
            )

    add_turns(12)
    original = list(db.storage[Message])
    provider = Summarizer()
    service = CompactionService(provider)
    _run(service.compact_session(db, session_id=session.id, user_id="local"))
    assert "IMPORTANT_END" in provider.prompts[0]
    assert all(m in db.storage[Message] for m in original)
    summary, active = _run(context_history(db, session.id))
    assert len(active) == 10
    previous = summary.summary["summary_text"]
    add_turns(6)
    _run(service.compact_session(db, session_id=session.id, user_id="local"))
    assert previous in provider.prompts[-1]
    saved = copy.deepcopy(summary.summary)
    add_turns(6)
    before = list(db.storage[Message])
    provider.fail = True
    with pytest.raises(ValueError, match="empty summary"):
        _run(service.compact_session(db, session_id=session.id, user_id="local"))
    assert summary.summary == saved
    assert db.storage[Message] == before


def test_model_switch_preflight_reserves_output_and_uses_full_context():
    from unittest.mock import AsyncMock
    from sentral.llm.generic.types import UserMessage
    from sentral.llm.model_limits import model_context

    db = FakeDB()
    session = Session(user_id="local", status="active", title="Switch")
    db.add(session)
    builder = SimpleNamespace(build=AsyncMock(return_value=[UserMessage(content="Earlier turn")]))
    provider = SimpleNamespace(
        model_context=lambda tier: model_context("claude-sonnet-5", 8192),
        count_input_tokens=AsyncMock(return_value=991809),
    )
    support = SimpleNamespace(
        provider=provider,
        context_builder=builder,
        tool_registry=SimpleNamespace(list_schemas=lambda: []),
    )
    context = make_fake_instance_context(app_db=db, agent_runtime_support=support)
    old = install_fake_db_overrides(app_db=db, instance_context=context)
    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        path = f"/api/v1/instances/main/sessions/{session.id}/model-context"
        response = client.post(path, json={"tier": "normal", "content": "Pending draft"})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["context_token_budget"] == 991808
        assert result["requires_compaction"] is True
        assert result["count_source"] == "provider_count"
        assert builder.build.call_args.kwargs["include_full_history"] is True
        assert provider.count_input_tokens.call_args.args[0][-1].content[0].text == "Pending draft"
        provider.count_input_tokens.return_value = 991808
        assert client.post(path, json={"tier": "normal"}).json()["requires_compaction"] is False
        provider.count_input_tokens.return_value = None
        result = client.post(path, json={"tier": "normal"}).json()
        assert result["count_source"] == "unavailable"
        assert result["input_tokens"] is None
        assert result["requires_compaction"] is None
        assert not db.storage.get(SessionSummary)
    finally:
        restore_test_app(old)
