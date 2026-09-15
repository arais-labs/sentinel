from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app.routers import sessions
from app.services.runtime.port_forwards import RuntimeForwardNotFound


@pytest.mark.asyncio
async def test_preview_target_resolves_only_requested_session_forward(monkeypatch):
    sid = uuid4()
    service = SimpleNamespace(get_session=AsyncMock())
    manager = SimpleNamespace(
        get_forward=AsyncMock(return_value=SimpleNamespace(local_port=51234, label="Preview"))
    )
    lookup = AsyncMock(return_value=manager)
    monkeypatch.setattr(sessions, "_resolve_session_service", lambda _: service)
    monkeypatch.setattr(sessions, "get_runtime_port_forward_manager", lookup)
    request = SimpleNamespace(state=SimpleNamespace(), path_params={"instance_name": "demo"})
    db = SimpleNamespace(close=AsyncMock())
    result = await sessions.get_runtime_forward_target(sid, "pf-123", request, db)
    db.close.assert_awaited_once()
    assert result == {"url": "http://127.0.0.1:51234/", "label": "Preview"}
    service.get_session.assert_awaited_once_with(db, session_id=sid, user_id="local")
    lookup.assert_awaited_once_with(session_id=sid, instance_name="demo")
    manager.get_forward.assert_awaited_once_with(session_id=str(sid), forward_id="pf-123")
    manager.get_forward.side_effect = RuntimeForwardNotFound()
    with pytest.raises(HTTPException) as error:
        await sessions.get_runtime_forward_target(sid, "pf-closed", request, db)
    assert error.value.status_code == 404
