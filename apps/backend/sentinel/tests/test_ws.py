import os
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
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


def test_ws_connect_send_ack_and_rejections():
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
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
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
                }
            )
            ack = ws.receive_json()
            assert ack["type"] == "message_ack"
            assert ack["session_id"] == session_id
            assert ack["content"] == "hello from ws"
            assert ack["metadata"]["source"] == "web"
            assert len(ack["metadata"]["attachments"]) == 1

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
        assert len(stored["metadata"]["attachments"]) == 1

        with pytest.raises(WebSocketDisconnect) as missing_token:
            with client.websocket_connect(f"/ws/sessions/{session_id}/stream"):
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
