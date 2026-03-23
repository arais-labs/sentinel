import os
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import Message, Session, SessionBinding, ToolApproval
from app.services.llm.generic.types import AssistantMessage, SystemMessage, TextContent, UserMessage
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


def test_sessions_crud_and_ownership():
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
        user1_token_resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert user1_token_resp.status_code == 200
        user1_token = user1_token_resp.json()["access_token"]

        user2_token = _make_token(sub="other-user")

        s1 = client.post("/api/v1/sessions", json={"title": "alpha"}, headers={"Authorization": f"Bearer {user1_token}"})
        s2 = client.post("/api/v1/sessions", json={"title": "beta"}, headers={"Authorization": f"Bearer {user1_token}"})
        s_child = client.post(
            "/api/v1/sessions",
            json={"title": "sub-agent:child"},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        s3 = client.post("/api/v1/sessions", json={"title": "gamma"}, headers={"Authorization": f"Bearer {user2_token}"})
        assert s1.status_code == 200 and s2.status_code == 200 and s3.status_code == 200 and s_child.status_code == 200

        session1_id = s1.json()["id"]
        session2_id = s2.json()["id"]
        child_session_id = s_child.json()["id"]
        session3_id = s3.json()["id"]

        # Mark one session as a child run (sub-agent session) and ensure it is hidden from top-level listing.
        for item in fake_db.storage[Session]:
            if str(item.id) == child_session_id:
                item.parent_session_id = uuid.UUID(session1_id)
                break

        list_user1 = client.get("/api/v1/sessions", headers={"Authorization": f"Bearer {user1_token}"})
        assert list_user1.status_code == 200
        ids_user1 = {item["id"] for item in list_user1.json()["items"]}
        assert session1_id in ids_user1
        assert session2_id in ids_user1
        assert child_session_id not in ids_user1
        assert session3_id not in ids_user1

        forbidden_get = client.get(f"/api/v1/sessions/{session3_id}", headers={"Authorization": f"Bearer {user1_token}"})
        assert forbidden_get.status_code == 404

        set_main_resp = client.post(
            f"/api/v1/sessions/{session2_id}/main",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert set_main_resp.status_code == 200
        assert set_main_resp.json()["is_main"] is True

        delete_resp = client.delete(
            f"/api/v1/sessions/{session1_id}",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert delete_resp.status_code == 200
        assert delete_resp.json()["status"] == "deleted"
        deleted_session = client.get(
            f"/api/v1/sessions/{session1_id}",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert deleted_session.status_code == 404

        m1 = client.post(
            f"/api/v1/sessions/{session2_id}/messages",
            json={"role": "user", "content": "first", "metadata": {}},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        m2 = client.post(
            f"/api/v1/sessions/{session2_id}/messages",
            json={"role": "system", "content": "second", "metadata": {}},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        m3 = client.post(
            f"/api/v1/sessions/{session2_id}/messages",
            json={"role": "user", "content": "third", "metadata": {}},
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert m1.status_code == 200 and m2.status_code == 200 and m3.status_code == 200

        history = client.get(
            f"/api/v1/sessions/{session2_id}/messages?limit=2",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert history.status_code == 200
        payload = history.json()
        assert len(payload["items"]) == 2
        assert payload["has_more"] is True

        stop_resp = client.post(
            f"/api/v1/sessions/{session2_id}/stop",
            headers={"Authorization": f"Bearer {user1_token}"},
        )
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] in {"stopping", "idle"}
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_session_rename_endpoint():
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
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        created = client.post("/api/v1/sessions", json={"title": "alpha"}, headers=headers)
        assert created.status_code == 200
        session_id = created.json()["id"]

        renamed = client.patch(
            f"/api/v1/sessions/{session_id}",
            json={"title": "   Better Name   "},
            headers=headers,
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Better Name"

        cleared = client.patch(
            f"/api/v1/sessions/{session_id}",
            json={"title": "   "},
            headers=headers,
        )
        assert cleared.status_code == 200
        assert cleared.json()["title"] is None
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_cannot_set_telegram_channel_session_as_main():
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
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        main_resp = client.get("/api/v1/sessions/default", headers=headers)
        assert main_resp.status_code == 200
        main_session_id = main_resp.json()["id"]

        channel_resp = client.post("/api/v1/sessions", json={"title": "TG Group · Ops"}, headers=headers)
        assert channel_resp.status_code == 200
        channel_session_id = channel_resp.json()["id"]

        import jwt as _jwt
        _decoded = _jwt.decode(token, options={"verify_signature": False})
        _actual_user_id = _decoded["sub"]
        fake_db.add(
            SessionBinding(
                user_id=_actual_user_id,
                binding_type="telegram_group",
                binding_key="group:-100123",
                session_id=uuid.UUID(channel_session_id),
                is_active=True,
                metadata_json={"chat_id": -100123},
            )
        )

        forbidden = client.post(f"/api/v1/sessions/{channel_session_id}/main", headers=headers)
        assert forbidden.status_code == 400
        payload = forbidden.json()
        detail = (
            payload.get("detail")
            or (payload.get("error") or {}).get("message")
            or str(payload)
        )
        assert "Telegram channel sessions cannot be set as main" in detail

        still_main = client.get(f"/api/v1/sessions/{main_session_id}", headers=headers)
        assert still_main.status_code == 200
        assert still_main.json()["is_main"] is True
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_cannot_rename_telegram_channel_session():
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
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        channel_resp = client.post("/api/v1/sessions", json={"title": "TG Group · Ops"}, headers=headers)
        assert channel_resp.status_code == 200
        channel_session_id = channel_resp.json()["id"]

        import jwt as _jwt
        _decoded = _jwt.decode(token, options={"verify_signature": False})
        _actual_user_id = _decoded["sub"]
        fake_db.add(
            SessionBinding(
                user_id=_actual_user_id,
                binding_type="telegram_group",
                binding_key="group:-100123",
                session_id=uuid.UUID(channel_session_id),
                is_active=True,
                metadata_json={"chat_id": -100123},
            )
        )

        rename = client.patch(
            f"/api/v1/sessions/{channel_session_id}",
            json={"title": "Renamed"},
            headers=headers,
        )
        assert rename.status_code == 400
        payload = rename.json()
        detail = (
            payload.get("detail")
            or (payload.get("error") or {}).get("message")
            or str(payload)
        )
        assert "cannot be renamed" in detail.lower()
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_reset_default_session_keeps_previous_main_runtime_workspace():
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
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_base = Path(tmpdir)
            with patch("app.services.session_runtime._RUNTIME_BASE_DIR", runtime_base):
                client = TestClient(app)
                login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
                assert login.status_code == 200
                token = login.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                main_resp = client.get("/api/v1/sessions/default", headers=headers)
                assert main_resp.status_code == 200
                old_main_id = main_resp.json()["id"]

                old_workspace = runtime_base / old_main_id / "workspace"
                old_workspace.mkdir(parents=True, exist_ok=True)
                marker = old_workspace / "keep.txt"
                marker.write_text("preserve")

                reset_resp = client.post("/api/v1/sessions/default/reset", headers=headers)
                assert reset_resp.status_code == 200
                new_main_id = reset_resp.json()["id"]
                assert new_main_id != old_main_id

                assert old_workspace.exists() is True
                assert marker.exists() is True
                assert marker.read_text() == "preserve"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_stop_session_generation_cancels_pending_git_approvals():
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
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "stop-cancels-approvals"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = uuid.UUID(session_resp.json()["id"])

        pending = ToolApproval(
            provider="git_exec",
            tool_name="git_exec",
            session_id=session_id,
            action="git_exec.run_write",
            description="Execute an approval-gated git or supported gh write command inside the session workspace.",
            status="pending",
            requested_by="session:test",
            payload_json={"tool_name": "git_exec"},
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        fake_db.add(pending)

        stop_resp = client.post(f"/api/v1/sessions/{session_id}/stop", headers=headers)
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] in {"stopping", "idle"}
        assert pending.status == "cancelled"
        assert pending.resolved_at is not None
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_stop_session_generation_materializes_unresolved_tool_calls():
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
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "stop-materializes-tool-result"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = uuid.UUID(session_resp.json()["id"])

        fake_db.add(
            Message(
                session_id=session_id,
                role="assistant",
                content="",
                metadata_json={
                    "generation": {
                        "requested_tier": "normal",
                        "resolved_model": "gpt-4.1-mini",
                        "provider": "openai",
                        "temperature": 0.7,
                        "max_iterations": 50,
                    },
                    "tool_calls": [
                        {
                            "id": "toolu_pending_runtime",
                            "name": "runtime_exec",
                            "arguments": {"command": "run_user", "shell_command": "sleep 20"},
                        }
                    ]
                },
            )
        )

        stop_resp = client.post(f"/api/v1/sessions/{session_id}/stop", headers=headers)
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] in {"stopping", "idle"}

        messages_resp = client.get(f"/api/v1/sessions/{session_id}/messages", headers=headers)
        assert messages_resp.status_code == 200
        items = messages_resp.json()["items"]
        materialized = next(
            (
                item
                for item in items
                if item["role"] == "tool_result"
                and item.get("tool_call_id") == "toolu_pending_runtime"
                and item.get("tool_name") == "runtime_exec"
            ),
            None,
        )
        assert materialized is not None
        assert materialized["metadata"]["cancelled_by_stop"] is True
        assert materialized["metadata"]["pending"] is False
        generation = materialized["metadata"].get("generation") or {}
        assert generation.get("resolved_model") == "gpt-4.1-mini"
        assert generation.get("provider") == "openai"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_context_usage_prefers_rebuilt_context_when_runtime_snapshot_missing():
    fake_db = FakeDB()

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    class _FakeContextBuilder:
        async def build(self, db, session_id, system_prompt=None, pending_user_message=None):
            _ = (db, session_id, system_prompt, pending_user_message)
            return [
                SystemMessage(content="You are Sentinel."),
                UserMessage(content="latest user"),
                AssistantMessage(content=[TextContent(text="latest answer")]),
            ]

    class _FakeLoop:
        def __init__(self):
            self.context_builder = _FakeContextBuilder()

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        old_agent_loop = getattr(app.state, "agent_loop", None)
        app.state.agent_loop = _FakeLoop()

        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "usage-rebuild"}, headers=headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        usage_resp = client.get(f"/api/v1/sessions/{session_id}/context-usage", headers=headers)
        assert usage_resp.status_code == 200
        payload = usage_resp.json()
        assert payload["source"] == "rebuilt_context_estimate"
        assert isinstance(payload["estimated_context_tokens"], int)
        assert payload["estimated_context_tokens"] > 0
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        if "old_agent_loop" in locals():
            app.state.agent_loop = old_agent_loop


def test_runtime_file_explorer_endpoints():
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
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        created = client.post("/api/v1/sessions", json={"title": "runtime-explorer"}, headers=headers)
        assert created.status_code == 200
        session_id = created.json()["id"]

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_base = Path(temp_dir)
            workspace = runtime_base / session_id / "workspace"
            (workspace / "src").mkdir(parents=True, exist_ok=True)
            (workspace / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (workspace / "README.md").write_text("# demo\n", encoding="utf-8")
            (workspace / "repo").mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "-C", str(workspace / "repo"), "init"], check=False)

            with patch("app.services.session_runtime._RUNTIME_BASE_DIR", runtime_base):
                files_root = client.get(f"/api/v1/sessions/{session_id}/runtime/files", headers=headers)
                assert files_root.status_code == 200
                root_payload = files_root.json()
                names = {item["name"] for item in root_payload["entries"]}
                assert {"src", "README.md", "repo"} <= names
                repo_entry = next(item for item in root_payload["entries"] if item["name"] == "repo")
                assert repo_entry["kind"] == "directory"
                assert repo_entry["is_git_root"] is True

                files_src = client.get(
                    f"/api/v1/sessions/{session_id}/runtime/files?path=src",
                    headers=headers,
                )
                assert files_src.status_code == 200
                src_payload = files_src.json()
                assert src_payload["path"] == "src"
                assert src_payload["parent_path"] == ""
                assert any(item["name"] == "main.py" and item["kind"] == "file" for item in src_payload["entries"])

                preview = client.get(
                    f"/api/v1/sessions/{session_id}/runtime/file?path=src/main.py",
                    headers=headers,
                )
                assert preview.status_code == 200
                preview_payload = preview.json()
                assert preview_payload["name"] == "main.py"
                assert "print('ok')" in preview_payload["content"]

                forbidden = client.get(
                    f"/api/v1/sessions/{session_id}/runtime/files?path=../secrets",
                    headers=headers,
                )
                assert forbidden.status_code == 400
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
