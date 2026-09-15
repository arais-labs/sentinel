from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.services.modules.builtins.notification import module
from app.services.notifications import Notification, publish_notification
from app.services.tools.registry import ToolRuntimeContext
from app.services.tools.registry_builder import build_default_registry


@pytest.mark.asyncio
async def test_agent_can_notify_without_workspace_and_cannot_spoof_target(monkeypatch):
    publish = AsyncMock(return_value={"id": "notification-id"})
    monkeypatch.setattr(module, "publish_notification", publish)
    runtime = ToolRuntimeContext(session_id=uuid4(), instance_name="main")
    result = await module.send(
        {"title": "Deadline", "message": "Review is due now", "severity": "urgent"}, runtime
    )
    assert result["status"] == "delivered"
    sent = publish.call_args.args[0]
    assert sent.target == {"instanceName": "main", "sessionId": str(runtime.session_id)}
    assert sent.severity == "urgent"
    with pytest.raises(ValidationError):
        await module.send(
            {"title": "X", "message": "Y", "target": {"sessionId": str(uuid4())}}, runtime
        )
    with pytest.raises(ValidationError):
        await module.send({"title": " ", "message": "Y"}, runtime)


@pytest.mark.asyncio
async def test_delivery_errors_are_reported_not_claimed_as_success(monkeypatch):
    monkeypatch.setattr(
        module, "publish_notification", AsyncMock(side_effect=ValueError("Unavailable"))
    )
    with pytest.raises(ValueError, match="Unavailable"):
        await module.send(
            {"title": "X", "message": "Y"},
            ToolRuntimeContext(session_id=uuid4(), instance_name="main"),
        )


@pytest.mark.asyncio
async def test_desktop_transport_authentication_and_payload(monkeypatch):
    from app.services import notifications

    def handler(request):
        assert request.url.path == "/v1/notifications"
        assert request.headers["x-sentinel-desktop-token"] == "test-token"
        return httpx.Response(200, json={"id": "saved"})

    monkeypatch.setattr(
        notifications,
        "settings",
        SimpleNamespace(
            workspace_runtime_socket="/tmp/test.sock", sentinel_desktop_token="test-token"
        ),
    )
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(handler))
    assert await publish_notification(
        Notification(source="test", title="Hello", message="World")
    ) == {"id": "saved"}


def test_notification_is_discoverable_in_default_registry():
    registry = build_default_registry()
    assert registry.get("notification") is not None
