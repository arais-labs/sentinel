from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.workspaces import lifecycle


@pytest.mark.asyncio
async def test_explicit_reconnect_retries_failed_setup_once(monkeypatch):
    workspace = uuid4()
    snapshots = AsyncMock(
        side_effect=[
            {str(workspace): {"state": "failed", "error": "Previous startup failed"}},
            {str(workspace): {"state": "running"}},
        ]
    )
    start = AsyncMock()
    monkeypatch.setattr(lifecycle, "statuses", snapshots)
    monkeypatch.setattr(lifecycle, "start", start)
    await lifecycle.ensure_ready(workspace, "/project", ["node"], retry=True)
    start.assert_awaited_once_with(workspace, "/project", ["node"])


@pytest.mark.asyncio
async def test_failed_setup_does_not_retry_during_ordinary_tool_calls(monkeypatch):
    workspace = uuid4()
    start = AsyncMock()
    monkeypatch.setattr(
        lifecycle,
        "statuses",
        AsyncMock(return_value={str(workspace): {"state": "failed", "error": "Startup failed"}}),
    )
    monkeypatch.setattr(lifecycle, "start", start)
    with pytest.raises(lifecycle.WorkspaceLifecycleError, match="Startup failed"):
        await lifecycle.ensure_ready(workspace, "/project", [])
    start.assert_not_awaited()
