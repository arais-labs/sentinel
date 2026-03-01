import os
import uuid

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import SubAgentTask
from app.services.sub_agents import SubAgentOrchestrator
from app.services.ws_manager import ConnectionManager
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


def test_sub_agents_crud_ownership_and_concurrency_cap():
    fake_db = FakeDB()
    ws_events: list[dict] = []

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    class _WsStub(ConnectionManager):
        async def broadcast_sub_agent_started(self, session_id: str, task_id: str, objective: str) -> None:
            ws_events.append(
                {"session_id": session_id, "task_id": task_id, "objective": objective}
            )

    old_init = app_main.init_db
    old_orchestrator = getattr(app.state, "sub_agent_orchestrator", None)
    old_ws_manager = getattr(app.state, "ws_manager", None)
    app_main.init_db = _noop_init_db
    app.state.sub_agent_orchestrator = SubAgentOrchestrator()
    app.state.ws_manager = _WsStub()
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        assert login.status_code == 200
        owner_token = login.json()["access_token"]
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        other_token = _make_token(sub="other-user")
        other_headers = {"Authorization": f"Bearer {other_token}"}

        session_resp = client.post("/api/v1/sessions", json={"title": "agent-work"}, headers=owner_headers)
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        created = client.post(
            f"/api/v1/sessions/{session_id}/sub-agents",
            json={
                "name": "collect evidence",
                "scope": "session notes",
                "browser_tab_id": "t2",
                "max_steps": 4,
            },
            headers=owner_headers,
        )
        assert created.status_code == 202
        created_payload = created.json()
        assert created_payload["status"] == "completed"
        assert created_payload["browser_tab_id"] == "t2"
        task_id = created_payload["id"]
        assert any(item["task_id"] == task_id for item in ws_events)

        listed = client.get(f"/api/v1/sessions/{session_id}/sub-agents", headers=owner_headers)
        assert listed.status_code == 200
        assert listed.json()["total"] >= 1
        assert any(item["id"] == task_id for item in listed.json()["items"])
        assert any(item.get("browser_tab_id") == "t2" for item in listed.json()["items"])

        detail = client.get(f"/api/v1/sessions/{session_id}/sub-agents/{task_id}", headers=owner_headers)
        assert detail.status_code == 200
        assert detail.json()["id"] == task_id

        forbidden = client.get(f"/api/v1/sessions/{session_id}/sub-agents/{task_id}", headers=other_headers)
        assert forbidden.status_code == 404

        cancel = client.delete(f"/api/v1/sessions/{session_id}/sub-agents/{task_id}", headers=owner_headers)
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"

        post_cancel_detail = client.get(
            f"/api/v1/sessions/{session_id}/sub-agents/{task_id}",
            headers=owner_headers,
        )
        assert post_cancel_detail.status_code == 200
        assert post_cancel_detail.json()["status"] == "cancelled"

        session_uuid = uuid.UUID(session_id)
        for i in range(3):
            fake_db.add(
                SubAgentTask(
                    session_id=session_uuid,
                    objective=f"pending-{i}",
                    constraints=[],
                    allowed_tools=[],
                    max_turns=3,
                    status="pending",
                )
            )

        capped = client.post(
            f"/api/v1/sessions/{session_id}/sub-agents",
            json={"name": "overflow", "scope": "x", "max_steps": 2},
            headers=owner_headers,
        )
        assert capped.status_code == 429
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        if old_orchestrator is not None:
            app.state.sub_agent_orchestrator = old_orchestrator
        if old_ws_manager is not None:
            app.state.ws_manager = old_ws_manager
        elif hasattr(app.state, "ws_manager"):
            delattr(app.state, "ws_manager")
