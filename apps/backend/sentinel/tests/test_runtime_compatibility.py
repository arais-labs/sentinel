from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request

from app.middleware.error_handler import register_error_handlers
from app.services.runtime import control, workspace_containers
from app.services.runtime.compatibility import RuntimeCompatibilityError


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["runtime_update_required", "app_update_required"])
@pytest.mark.parametrize("operation", ["status", "start"])
async def test_runtime_compatibility_survives_desktop_and_api_error_handling(
    monkeypatch, code, operation
):
    machine_id = uuid4()
    failure = RuntimeCompatibilityError(
        code, "Action required", machine_id=machine_id, installed=1, required=2
    )
    manager = SimpleNamespace(
        status=AsyncMock(side_effect=failure), ensure_session_desktop=AsyncMock(side_effect=failure)
    )
    monkeypatch.setattr(control, "runtime_configured", AsyncMock(return_value=True))
    monkeypatch.setattr(control, "_runtime_session_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(control, "get_runtime_desktop_manager", AsyncMock(return_value=manager))
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/instances/test/runtime/live-view")
    async def live_view(request: Request):
        handler = (
            control.live_view_response
            if operation == "status"
            else control.set_live_view_resolution_response
        )
        return await handler(
            request=request,
            session_id=str(uuid4()),
            db=AsyncMock(),
            geometry="1920x1200",
            resolution_presets={"1920x1200"},
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/instances/test/runtime/live-view")
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": code,
        "message": "Action required",
        "details": {"machine_id": str(machine_id), "installed": 1, "required": 2},
    }


@pytest.mark.asyncio
async def test_container_router_keeps_typed_runtime_failure(monkeypatch):
    failure = RuntimeCompatibilityError("app_update_required", "Restart Sentinel")
    monkeypatch.setattr(workspace_containers, "local_request", AsyncMock(side_effect=failure))
    monkeypatch.setattr(workspace_containers, "remote_for", AsyncMock(return_value=None))
    for operation in [
        workspace_containers.request("exec", workspace=str(uuid4())),
        workspace_containers.statuses(uuid4()),
    ]:
        with pytest.raises(RuntimeCompatibilityError) as caught:
            await operation
        assert caught.value is failure
