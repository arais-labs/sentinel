from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Request

from app.middleware.error_handler import register_error_handlers
from app.services.workspaces import control
from app.services.workspaces import lifecycle
from app.services.workspaces.compatibility import RuntimeCompatibilityError


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["runtime_update_required", "app_update_required"])
async def test_runtime_compatibility_survives_desktop_and_api_error_handling(monkeypatch, code):
    machine_id = uuid4()
    failure = RuntimeCompatibilityError(
        code, "Action required", machine_id=machine_id, installed=1, required=2
    )
    manager = SimpleNamespace(
        status=AsyncMock(side_effect=failure), ensure_session_desktop=AsyncMock(side_effect=failure)
    )
    monkeypatch.setattr(control, "workspace_attached", AsyncMock(return_value=True))
    monkeypatch.setattr(control, "_session_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(control, "get_desktop_manager", AsyncMock(return_value=manager))
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/instances/test/sessions/test/workspace/desktop")
    async def live_view(request: Request):
        return await control.desktop_response(
            request=request, session_id=str(uuid4()), db=AsyncMock()
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/instances/test/sessions/test/workspace/desktop")
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": code,
        "message": "Action required",
        "details": {"machine_id": str(machine_id), "installed": 1, "required": 2},
    }


@pytest.mark.asyncio
async def test_container_router_keeps_typed_runtime_failure(monkeypatch):
    failure = RuntimeCompatibilityError("app_update_required", "Restart Sentinel")
    monkeypatch.setattr(lifecycle, "local_request", AsyncMock(side_effect=failure))
    monkeypatch.setattr(lifecycle, "remote_for", AsyncMock(return_value=None))
    for operation in [
        lifecycle.request("exec", workspace=str(uuid4())),
        lifecycle.statuses(uuid4()),
    ]:
        with pytest.raises(RuntimeCompatibilityError) as caught:
            await operation
        assert caught.value is failure
