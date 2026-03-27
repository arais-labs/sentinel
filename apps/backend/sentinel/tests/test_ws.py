import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import Message, ToolApproval
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.ws.ws_stream_service import unresolved_tool_calls_from_history
from tests.fake_db import FakeDB


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


class _AlwaysRunningRegistry(AgentRunRegistry):
    async def is_running(self, session_id: str) -> bool:  # noqa: ARG002
        return True


def test_ws_connect_send_ack_and_rejections():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    old_agent_runtime_support = getattr(app.state, "agent_runtime_support", None)
    from app.routers import sessions as sessions_router

    async def _noop_provision_runtime(session_id, ws_manager=None):  # noqa: ARG001
        return None

    old_provision_runtime = sessions_router._provision_runtime
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.state.agent_runtime_support = None
    sessions_router._provision_runtime = _noop_provision_runtime

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        owner_token = login.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "ws-test"}, headers=owner_headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={owner_token}") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            assert connected["session_id"] == session_id

            ws.send_json(
                {
                    "type": "message",
                    "content": "hello from ws",
                    "attachments": [
                        {
                            "mime_type": "image/png",
                            "base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9s8bVgAAAABJRU5ErkJggg==",
                            "filename": "pixel.png",
                        }
                    ],
                    "agent_mode": "read_only",
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "message_ack"
            assert ack["session_id"] == session_id
            assert ack["content"] == "hello from ws"
            assert ack["metadata"]["source"] == "web"
            assert ack["metadata"]["agent_mode"] == "read_only"
            assert len(ack["metadata"]["attachments"]) == 1
            generation = ack["metadata"].get("generation") or {}
            assert generation.get("requested_tier") == "normal"
            assert generation.get("temperature") == 0.7
            assert isinstance(generation.get("max_iterations"), int)
            assert generation.get("max_iterations") > 0

            # No provider configured in tests -> explicit agent error for UI feedback.
            no_provider = ws.receive_json()
            assert no_provider["type"] == "agent_error"
            assert "No provider connected" in no_provider["message"]
            done = ws.receive_json()
            assert done["type"] == "done"
            assert done["stop_reason"] == "error"

        messages = client.get(f"/api/v1/sessions/{session_id}/messages", headers=owner_headers)
        assert messages.status_code == 200
        stored = next(item for item in messages.json()["items"] if item["content"] == "hello from ws")
        assert stored["metadata"]["source"] == "web"
        assert stored["metadata"]["agent_mode"] == "read_only"
        assert len(stored["metadata"]["attachments"]) == 1
        stored_generation = stored["metadata"].get("generation") or {}
        assert stored_generation.get("requested_tier") == "normal"
        assert stored_generation.get("temperature") == 0.7
        assert isinstance(stored_generation.get("max_iterations"), int)
        assert stored_generation.get("max_iterations") > 0

        anon_client = TestClient(app)
        with pytest.raises(WebSocketDisconnect) as missing_token:
            with anon_client.websocket_connect(f"/ws/sessions/{session_id}/stream"):
                pass
        assert missing_token.value.code == 4001

        with pytest.raises(WebSocketDisconnect) as bad_token:
            with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token=invalid-token"):
                pass
        assert bad_token.value.code == 4001

        other_token = _make_token(sub="other-user")
        with pytest.raises(WebSocketDisconnect) as forbidden:
            with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={other_token}"):
                pass
        assert forbidden.value.code == 4004

        unknown_session = uuid.uuid4()
        with pytest.raises(WebSocketDisconnect) as unknown:
            with client.websocket_connect(f"/ws/sessions/{unknown_session}/stream?token={owner_token}"):
                pass
        assert unknown.value.code == 4004
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        app.state.agent_runtime_support = old_agent_runtime_support
        sessions_router._provision_runtime = old_provision_runtime


def test_ws_rejects_invalid_agent_mode_payload():
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

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        owner_token = login.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "ws-invalid-mode"}, headers=owner_headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={owner_token}") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            ws.send_json({"type": "message", "content": "hello", "agent_mode": "invalid-mode"})
            error = ws.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "invalid_payload"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_ws_connected_rehydrates_unresolved_tool_calls():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    old_run_registry = getattr(app.state, "agent_run_registry", None)
    app.state.agent_run_registry = _AlwaysRunningRegistry()
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        owner_token = login.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "ws-pending"}, headers=owner_headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        fake_db.add(
            Message(
                session_id=uuid.UUID(session_id),
                role="assistant",
                content="",
                metadata_json={
                    "tool_calls": [
                        {
                            "id": "toolu_pending_1",
                            "name": "git",
                            "arguments": {"command": "write", "cli_command": "git push origin main"},
                        }
                    ]
                },
            )
        )
        fake_db.add(
            ToolApproval(
                provider="git",
                tool_name="git",
                session_id=uuid.UUID(session_id),
                action="git.write",
                description="Execute an approval-gated git or supported gh write command inside the session workspace.",
                status="pending",
                requested_by="session:test",
                payload_json={"tool_name": "git"},
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )

        with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={owner_token}") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            assert connected["session_id"] == session_id

            replay_start = ws.receive_json()
            assert replay_start["type"] == "toolcall_start"
            assert replay_start["tool_call"]["id"] == "toolu_pending_1"
            assert replay_start["tool_call"]["name"] == "git"

            replay_pending = ws.receive_json()
            assert replay_pending["type"] == "tool_result"
            assert replay_pending["tool_result"]["tool_call_id"] == "toolu_pending_1"
            assert replay_pending["tool_result"]["tool_arguments"] == {"command": "write", "cli_command": "git push origin main"}
            assert replay_pending["tool_result"]["content"]["status"] == "running"
            assert "pending" not in replay_pending["tool_result"]["metadata"]
            assert "approval" not in replay_pending["tool_result"]["metadata"]
    finally:
        if old_run_registry is None:
            delattr(app.state, "agent_run_registry")
        else:
            app.state.agent_run_registry = old_run_registry
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_unresolved_tool_calls_ignore_calls_with_persisted_tool_result():
    history = [
        {
            "id": "assistant-1",
            "role": "assistant",
            "metadata": {
                "tool_calls": [
                    {
                        "id": "toolu_pending_1",
                        "name": "git",
                        "arguments": {"command": "write", "cli_command": "git push origin main"},
                    }
                ]
            },
        },
        {
            "id": "result-1",
            "role": "tool_result",
            "tool_call_id": "toolu_pending_1",
            "tool_name": "git",
            "metadata": {
                "approval": {
                    "provider": "git",
                    "approval_id": "approval-1",
                    "status": "pending",
                    "pending": True,
                    "can_resolve": True,
                }
            },
        },
    ]

    assert unresolved_tool_calls_from_history(history) == []


def test_ws_connected_history_includes_pending_tool_result_for_approval():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    old_run_registry = getattr(app.state, "agent_run_registry", None)
    app.state.agent_run_registry = _AlwaysRunningRegistry()
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        owner_token = login.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "ws-pending-truncated"}, headers=owner_headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        fake_db.add(
            Message(
                session_id=uuid.UUID(session_id),
                role="assistant",
                content="",
                metadata_json={
                    "tool_calls": [
                        {
                            "id": "toolu_pending_2",
                            "name": "git",
                            "arguments": {
                                "command": "write",
                                "cli_command": "gh pr create --repo exampleco/exampleco-gitops --title Test --body Body",
                            },
                        }
                    ]
                },
            )
        )
        fake_db.add(
            Message(
                session_id=uuid.UUID(session_id),
                role="tool_result",
                tool_call_id="toolu_pending_2",
                tool_name="git",
                content='{"status":"pending","message":"Action requires approval."}',
                metadata_json={
                    "pending": True,
                    "approval": {
                        "provider": "git",
                        "approval_id": str(uuid.uuid4()),
                        "status": "pending",
                        "pending": True,
                        "can_resolve": True,
                        "label": "Git write approval",
                    },
                },
            )
        )

        with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={owner_token}") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            assert connected["session_id"] == session_id
            history = connected["history"]
            pending_result = next(item for item in history if item["role"] == "tool_result")
            assert pending_result["tool_call_id"] == "toolu_pending_2"
            approval = pending_result["metadata"].get("approval")
            assert isinstance(approval, dict)
            assert approval.get("provider") == "git"
            assert approval.get("pending") is True
    finally:
        if old_run_registry is None:
            delattr(app.state, "agent_run_registry")
        else:
            app.state.agent_run_registry = old_run_registry
        app.dependency_overrides.clear()
        app_main.init_db = old_init



def test_ws_connected_rehydrates_unresolved_non_git_tool_calls():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    old_run_registry = getattr(app.state, "agent_run_registry", None)
    app.state.agent_run_registry = _AlwaysRunningRegistry()
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        owner_token = login.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "ws-runtime-pending"}, headers=owner_headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        fake_db.add(
            Message(
                session_id=uuid.UUID(session_id),
                role="assistant",
                content="",
                metadata_json={
                    "tool_calls": [
                        {
                            "id": "toolu_pending_runtime",
                            "name": "runtime_exec",
                            "arguments": {"command": "run_user", "shell_command": "sleep 10"},
                        }
                    ]
                },
            )
        )

        with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={owner_token}") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            assert connected["session_id"] == session_id

            replay_start = ws.receive_json()
            assert replay_start["type"] == "toolcall_start"
            assert replay_start["tool_call"]["id"] == "toolu_pending_runtime"
            assert replay_start["tool_call"]["name"] == "runtime_exec"

            replay_pending = ws.receive_json()
            assert replay_pending["type"] == "tool_result"
            assert replay_pending["tool_result"]["tool_call_id"] == "toolu_pending_runtime"
            assert replay_pending["tool_result"]["tool_arguments"] == {"command": "run_user", "shell_command": "sleep 10"}
            assert replay_pending["tool_result"]["content"]["status"] == "running"
            assert "pending" not in replay_pending["tool_result"]["metadata"]
            assert "approval_id" not in replay_pending["tool_result"]["metadata"]
    finally:
        if old_run_registry is None:
            delattr(app.state, "agent_run_registry")
        else:
            app.state.agent_run_registry = old_run_registry
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_ws_connected_reconciles_stale_unresolved_calls_when_run_not_active():
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

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        owner_token = login.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "ws-stale-pending"}, headers=owner_headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        fake_db.add(
            Message(
                session_id=uuid.UUID(session_id),
                role="assistant",
                content="",
                metadata_json={
                    "generation": {
                        "requested_tier": "normal",
                        "resolved_model": "claude-sonnet-4-20250514",
                        "provider": "anthropic",
                        "temperature": 0.7,
                        "max_iterations": 50,
                    },
                    "tool_calls": [
                        {
                            "id": "toolu_stale_1",
                            "name": "git",
                            "arguments": {"command": "write", "cli_command": "git push origin main"},
                        }
                    ]
                },
            )
        )
        pending_approval = ToolApproval(
            provider="git",
            tool_name="git",
            session_id=uuid.UUID(session_id),
            action="git.write",
            description="Execute an approval-gated git or supported gh write command inside the session workspace.",
            status="pending",
            requested_by="session:test",
            payload_json={"tool_name": "git"},
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        fake_db.add(pending_approval)

        with client.websocket_connect(f"/ws/sessions/{session_id}/stream?token={owner_token}") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            history = connected.get("history") or []
            reconciled = next(
                (
                    item
                    for item in history
                    if item.get("role") == "tool_result"
                    and item.get("tool_call_id") == "toolu_stale_1"
                    and item.get("tool_name") == "git"
                ),
                None,
            )
            assert reconciled is not None
            metadata = reconciled.get("metadata") or {}
            assert metadata.get("interrupted") is True
            assert metadata.get("pending") is False
            generation = metadata.get("generation") or {}
            assert generation.get("resolved_model") == "claude-sonnet-4-20250514"
            assert generation.get("provider") == "anthropic"

        assert pending_approval.status == "cancelled"
        assert pending_approval.resolved_at is not None
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
