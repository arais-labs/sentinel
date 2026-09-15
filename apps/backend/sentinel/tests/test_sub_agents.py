import uuid

from fastapi.testclient import TestClient


from app.dependencies import get_db, get_manager_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import SubAgentTask
from app.services.sub_agents.orchestrator import SubAgentOrchestrator
from app.services.ws.ws_manager import ConnectionManager
from tests.fake_db import FakeDB


def test_sub_agents_crud_ownership_and_concurrency_cap():
    fake_db = FakeDB()
    ws_events: list[dict] = []

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    class _WsStub(ConnectionManager):
        async def broadcast_sub_agent_started(
            self, session_id: str, task_id: str, objective: str
        ) -> None:
            ws_events.append({"session_id": session_id, "task_id": task_id, "objective": objective})

    old_init = app_main.init_db
    old_ws_manager = getattr(app.state, "ws_manager", None)
    orchestrator = SubAgentOrchestrator()

    def _override_instance_runtime_context(_request):
        class _Context:
            sub_agent_orchestrator = orchestrator

        return _Context()

    from app.routers import sub_agents as sub_agents_router

    app_main.init_db = _noop_init_db
    old_get_runtime_context = sub_agents_router.get_request_instance_runtime_context
    sub_agents_router.get_request_instance_runtime_context = _override_instance_runtime_context
    app.state.ws_manager = _WsStub()
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        owner_headers = {"x-sentinel-desktop-token": "test-desktop-transport-token"}

        session_resp = client.post(
            "/api/v1/instances/main/sessions",
            json={"title": "agent-work"},
            headers=owner_headers,
        )
        assert session_resp.status_code == 200
        session_id = session_resp.json()["id"]

        created = client.post(
            f"/api/v1/instances/main/sessions/{session_id}/sub-agents",
            json={
                "name": "collect evidence",
                "scope": "session notes",
                "tier": "fast",
            },
            headers=owner_headers,
        )
        assert created.status_code == 202
        created_payload = created.json()
        assert created_payload["status"] == "failed"
        task_id = created_payload["id"]
        assert any(item["task_id"] == task_id for item in ws_events)

        listed = client.get(
            f"/api/v1/instances/main/sessions/{session_id}/sub-agents", headers=owner_headers
        )
        assert listed.status_code == 200
        assert listed.json()["total"] >= 1
        assert any(item["id"] == task_id for item in listed.json()["items"])

        detail = client.get(
            f"/api/v1/instances/main/sessions/{session_id}/sub-agents/{task_id}",
            headers=owner_headers,
        )
        assert detail.status_code == 200
        assert detail.json()["id"] == task_id

        cancel = client.delete(
            f"/api/v1/instances/main/sessions/{session_id}/sub-agents/{task_id}",
            headers=owner_headers,
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "failed"

        post_cancel_detail = client.get(
            f"/api/v1/instances/main/sessions/{session_id}/sub-agents/{task_id}",
            headers=owner_headers,
        )
        assert post_cancel_detail.status_code == 200
        assert post_cancel_detail.json()["status"] == "failed"

        session_uuid = uuid.UUID(session_id)
        for i in range(3):
            fake_db.add(
                SubAgentTask(
                    session_id=session_uuid,
                    objective=f"pending-{i}",
                    constraints=[],
                    allowed_tools=[],
                    status="pending",
                )
            )

        capped = client.post(
            f"/api/v1/instances/main/sessions/{session_id}/sub-agents",
            json={"name": "overflow", "scope": "x"},
            headers=owner_headers,
        )
        assert capped.status_code == 429
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
        sub_agents_router.get_request_instance_runtime_context = old_get_runtime_context
        if old_ws_manager is not None:
            app.state.ws_manager = old_ws_manager
        elif hasattr(app.state, "ws_manager"):
            delattr(app.state, "ws_manager")
