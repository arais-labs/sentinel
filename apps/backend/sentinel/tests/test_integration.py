import os
from types import SimpleNamespace

from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("TOOL_FILE_READ_BASE_DIR", "/tmp")

from app.main import app
from app.config import settings
from app.schemas.runtime import RuntimeProviderInfoResponse
from app.services.instance_runtime_context import InstanceRuntimeContext
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent, AssistantMessage, TextContent
from app.services.sub_agents import SubAgentOrchestrator
from app.services.tools import ToolExecutor, ToolRegistry
from app.services.triggers.trigger_scheduler import TriggerScheduler
from tests.fake_db import FakeDB
from tests.helpers import FakeSessionFactory, install_fake_db_overrides, restore_test_app


class _NoopProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "noop"

    async def chat(
        self,
        messages,
        model,
        tools=None,
        temperature=0.7,
        reasoning_config=None,
        tool_choice=None,
    ):
        return AssistantMessage(
            content=[TextContent(text="noop")],
            model=model,
            provider=self.name,
        )

    async def stream(
        self,
        messages,
        model,
        tools=None,
        temperature=0.7,
        reasoning_config=None,
        tool_choice=None,
    ):
        yield AgentEvent(type="start")
        yield AgentEvent(type="done", stop_reason="stop")


def test_full_integration_happy_path():
    fake_db = FakeDB()
    session_factory = FakeSessionFactory(fake_db)
    tool_registry = ToolRegistry()
    tool_executor = ToolExecutor(tool_registry)
    fake_runtime_support = SimpleNamespace(provider=_NoopProvider())
    instance_context = InstanceRuntimeContext(
        name="main",
        database_name="sentinel_main_test",
        instance_settings=settings,
        session_factory=session_factory,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        agent_runtime_support=fake_runtime_support,
        trigger_scheduler=TriggerScheduler(
            agent_runtime_support=fake_runtime_support,
            tool_executor=tool_executor,
            db_factory=None,
        ),
        sub_agent_orchestrator=SubAgentOrchestrator(),
        background_tasks=[],
    )

    from app.routers import sessions as sessions_router
    from app.routers import runtime as runtime_router
    from app.routers import ws as ws_router

    async def _noop_provision_runtime(session_id, ws_manager=None):  # noqa: ARG001
        return None

    async def _fake_runtime_provider_info(session_id: str) -> RuntimeProviderInfoResponse:  # noqa: ARG001
        return RuntimeProviderInfoResponse(id="test", label="Test", status="available", items=[])

    old_provision_runtime = sessions_router._provision_runtime
    old_runtime_provider_info = runtime_router._resolve_runtime_provider_info
    old_manager_session = ws_router.ManagerSessionLocal
    old_queue_runtime_activation = ws_router.queue_runtime_activation
    old_init = install_fake_db_overrides(
        app_db=fake_db,
        instance_context=instance_context,
        session_factory=session_factory,
    )
    sessions_router._provision_runtime = _noop_provision_runtime
    runtime_router._resolve_runtime_provider_info = _fake_runtime_provider_info
    ws_router.ManagerSessionLocal = session_factory
    ws_router.queue_runtime_activation = lambda _app, _session_id: False

    try:
        client = TestClient(app)

        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        created_session = client.post(
            "/api/v1/instances/main/sessions", json={"title": "integration-e2e"}, headers=headers
        )
        assert created_session.status_code == 200
        session_id = created_session.json()["id"]

        for i in range(12):
            sent = client.post(
                f"/api/v1/instances/main/sessions/{session_id}/messages",
                json={
                    "role": "user" if i % 2 == 0 else "system",
                    "content": f"integration message {i} " + " ".join(["detail"] * 12),
                    "metadata": {},
                },
                headers=headers,
            )
            assert sent.status_code == 200

        compacted = client.post(f"/api/v1/instances/main/sessions/{session_id}/compact", headers=headers)
        assert compacted.status_code == 200
        assert compacted.json()["raw_token_count"] > compacted.json()["compressed_token_count"]

        spawned = client.post(
            f"/api/v1/instances/main/sessions/{session_id}/sub-agents",
            json={"name": "triage blockers", "scope": "recent messages", "max_steps": 4},
            headers=headers,
        )
        assert spawned.status_code == 202
        task_id = spawned.json()["id"]

        task_list = client.get(f"/api/v1/instances/main/sessions/{session_id}/sub-agents", headers=headers)
        assert task_list.status_code == 200
        assert any(item["id"] == task_id for item in task_list.json()["items"])

        cancelled = client.delete(
            f"/api/v1/instances/main/sessions/{session_id}/sub-agents/{task_id}", headers=headers
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

        trigger = client.post(
            "/api/v1/instances/main/triggers",
            json={
                "name": "integration-trigger",
                "type": "cron",
                "config": {"cron": "*/15 * * * *"},
                "action_type": "agent_message",
                "action_config": {"message": "run"},
            },
            headers=headers,
        )
        assert trigger.status_code == 200
        trigger_id = trigger.json()["id"]

        fired = client.post(
            f"/api/v1/instances/main/triggers/{trigger_id}/fire",
            json={"input_payload": {"source": "integration"}},
            headers=headers,
        )
        assert fired.status_code == 200

        modules = client.get("/api/v1/instances/main/modules", headers=headers)
        assert modules.status_code == 200
        module_names = {item["name"] for item in modules.json()["modules"]}
        assert "runtime" in module_names

        live_view = client.get(
            "/api/v1/instances/main/runtime/live-view",
            headers=headers,
            params={"session_id": session_id},
        )
        assert live_view.status_code == 200
        assert "enabled" in live_view.json()

        with client.websocket_connect(f"/ws/instances/main/sessions/{session_id}/stream?token={token}") as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            ws.send_json({"type": "message", "content": "integration websocket message"})
            ack = ws.receive_json()
            assert ack["type"] == "message_ack"
            assert ack["content"] == "integration websocket message"

        config = client.get("/api/v1/instances/main/admin/config", headers=headers)
        assert config.status_code == 200

        audits = client.get("/api/v1/admin/audit", headers=headers)
        assert audits.status_code == 200
        assert audits.json()["total"] >= 1
    finally:
        restore_test_app(old_init)
        sessions_router._provision_runtime = old_provision_runtime
        runtime_router._resolve_runtime_provider_info = old_runtime_provider_info
        ws_router.ManagerSessionLocal = old_manager_session
        ws_router.queue_runtime_activation = old_queue_runtime_activation
