from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.workspaces.desktop.manager import DesktopManager
from app.services.workspaces.paths import WorkspaceLocation


@pytest.mark.parametrize(
    ("selection", "enabled"), [("none", False), ("xfce", True), ("windows", True)]
)
def test_desktop_manager_enables_any_catalog_desktop(selection, enabled):
    manager = DesktopManager(
        SimpleNamespace(ssh=None),
        workspace_location=SimpleNamespace(desktop=selection),
    )
    assert manager.enabled is enabled


@pytest.mark.asyncio
@pytest.mark.parametrize("desktop", ["windows", "xfce"])
async def test_a_desktop_runs_with_its_workspace_without_a_guest_probe(desktop):
    # sentineld reports the workspace running only once its desktop is up.
    transport = SimpleNamespace(
        workspace_status=AsyncMock(return_value={"state": "running", "screen": True}),
        run=AsyncMock(side_effect=AssertionError("no guest command for the desktop's state")),
    )
    manager = DesktopManager(
        SimpleNamespace(ssh=transport),
        workspace_location=WorkspaceLocation(
            "/project", "/var/lib/sentinel", desktop=desktop, features=frozenset({"shell"})
        ),
    )
    status = await manager.status()
    assert status["state"] == "running" and status["viewable"] is True
    transport.run.assert_not_called()
