import os
import uuid
import asyncio

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db, get_llm_provider
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import Message, Session, SessionSummary
from app.services.compaction import CompactionService
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent, AssistantMessage, TextContent
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _make_token(*, sub: str, role: str = "agent", agent_id: str = "agent-test") -> str:
    secret = os.getenv("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
    return jwt.encode(
        {
            "sub": sub,
            "role": role,
            "agent_id": agent_id,
            "exp": 1999999999,
            "iat": 1771810000,
            "jti": str(uuid.uuid4()),
            "token_type": "access",
        },
        secret,
        algorithm="HS256",
    )


class _NoopProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "noop"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        return AssistantMessage(content=[TextContent(text="noop")], model=model, provider=self.name)

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        yield AgentEvent(type="start")
        yield AgentEvent(type="done", stop_reason="stop")


def test_compaction_create_idempotent_and_ownership():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: _NoopProvider()

    try:
        client = TestClient(app)

        owner_login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        owner_headers = {"Authorization": f"Bearer {owner_login.json()['access_token']}"}
        other_headers = {"Authorization": f"Bearer {_make_token(sub='other-user')}"}

        session = client.post("/api/v1/sessions", json={"title": "compact-me"}, headers=owner_headers)
        assert session.status_code == 200
        session_id = session.json()["id"]

        for i in range(15):
            content = f"message {i} " + " ".join(["longtext"] * 24)
            role = "user" if i % 2 == 0 else "system"
            posted = client.post(
                f"/api/v1/sessions/{session_id}/messages",
                json={"role": role, "content": content, "metadata": {}},
                headers=owner_headers,
            )
            assert posted.status_code == 200

        compacted = client.post(f"/api/v1/sessions/{session_id}/compact", headers=owner_headers)
        assert compacted.status_code == 200
        payload = compacted.json()
        assert payload["session_id"] == session_id
        assert payload["raw_token_count"] > payload["compressed_token_count"]
        assert payload["raw_token_count"] > 0

        summaries = fake_db.storage[SessionSummary]
        assert len(summaries) == 1
        first_summary_id = summaries[0].id

        compacted_again = client.post(f"/api/v1/sessions/{session_id}/compact", headers=owner_headers)
        assert compacted_again.status_code == 200
        second_payload = compacted_again.json()
        assert second_payload["raw_token_count"] >= second_payload["compressed_token_count"]
        summaries_after = fake_db.storage[SessionSummary]
        assert len(summaries_after) == 1
        assert summaries_after[0].id == first_summary_id

        forbidden = client.post(f"/api/v1/sessions/{session_id}/compact", headers=other_headers)
        assert forbidden.status_code == 404
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_compaction_noop_when_context_is_small():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: _NoopProvider()

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        session = client.post("/api/v1/sessions", json={"title": "small-context"}, headers=headers)
        session_id = session.json()["id"]

        for i in range(5):
            posted = client.post(
                f"/api/v1/sessions/{session_id}/messages",
                json={"role": "user", "content": f"short {i}", "metadata": {}},
                headers=headers,
            )
            assert posted.status_code == 200

        compacted = client.post(f"/api/v1/sessions/{session_id}/compact", headers=headers)
        assert compacted.status_code == 200
        payload = compacted.json()
        assert payload["raw_token_count"] == 0
        assert payload["compressed_token_count"] == 0
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_compaction_retains_coherent_recent_turn_not_just_last_10_rows():
    fake_db = FakeDB()
    session = Session(user_id="dev-admin", status="active", title="turn-aware-retain")
    fake_db.add(session)

    # Older turn to compact away
    fake_db.add(Message(session_id=session.id, role="user", content="old-user-1", metadata_json={}))
    fake_db.add(Message(session_id=session.id, role="assistant", content="old-assistant-1", metadata_json={}))

    # Most recent turn: one user + many tool results + final assistant.
    # Legacy row-count retention would drop this user row when trimming to last 10 rows.
    fake_db.add(Message(session_id=session.id, role="user", content="latest-user-turn", metadata_json={}))
    for idx in range(15):
        fake_db.add(
            Message(
                session_id=session.id,
                role="tool_result",
                content=f'{{"status":"ok","idx":{idx}}}',
                metadata_json={"is_error": False},
                tool_call_id=f"tool_{idx}",
                tool_name="modules_discovery",
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
    assert result.raw_token_count > 0

    kept = [m for m in fake_db.storage[Message] if m.session_id == session.id]
    kept.sort(key=lambda m: m.created_at)
    assert len(kept) > 10
    assert any(m.role == "user" and m.content == "latest-user-turn" for m in kept)
    assert kept[0].role == "user"
    assert kept[0].content == "latest-user-turn"

    summaries = [s for s in fake_db.storage[SessionSummary] if s.session_id == session.id]
    assert len(summaries) == 1
    summary_payload = summaries[0].summary or {}
    assert summary_payload.get("active_message_count") == len(kept)
    assert summary_payload.get("compacted_message_count") == 2
