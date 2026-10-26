"""sentineld's catalog decides what a workspace can be and which guest features apply."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.workspaces import lifecycle
from app.services.workspaces import metrics as metrics_module
from app.services.workspaces.lifecycle import catalog as runtime_catalog
from tests.conftest import TEST_CATALOG
from tests.test_workspaces import workspace_app as workspace_app


@pytest.mark.asyncio
async def test_development_tools_pass_the_runtime_catalog_through(workspace_app, monkeypatch):
    from app.services.workspaces.machines import environment

    client, _, machine, *_ = workspace_app

    async def macos(_):
        return "darwin"

    calls = []

    async def local_request(action, **values):
        calls.append(action)
        return TEST_CATALOG

    monkeypatch.setattr(environment, "machine_os", macos)
    monkeypatch.setattr(lifecycle, "catalog", runtime_catalog)
    monkeypatch.setattr(lifecycle, "_catalogs", {})
    monkeypatch.setattr(lifecycle, "local_request", local_request)
    response = await client.get(f"machines/{machine.id}/development-tools")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "os": "darwin",
        "workspaces_available": True,
        **{key: TEST_CATALOG[key] for key in ("distributions", "desktops", "tools", "stacks")},
    }
    assert calls == ["catalog"]

    async def unavailable(action, **values):
        raise lifecycle.WorkspaceLifecycleError("Could not reach workspace services.")

    monkeypatch.setattr(lifecycle, "local_request", unavailable)
    response = await client.get(f"machines/{machine.id}/development-tools")
    assert response.status_code == 503
    assert "Could not reach workspace services." in response.json()["detail"]


@pytest.mark.asyncio
async def test_runtime_catalog_is_fetched_once(monkeypatch):
    local_request = AsyncMock(return_value=TEST_CATALOG)
    monkeypatch.setattr(lifecycle, "catalog", runtime_catalog)
    monkeypatch.setattr(lifecycle, "_catalogs", {})
    monkeypatch.setattr(lifecycle, "local_request", local_request)
    assert await lifecycle.distribution_features("shell-less") == frozenset()
    assert "shell" in await lifecycle.distribution_features("debian")
    assert "shell" in await lifecycle.distribution_features("windows-11")
    local_request.assert_awaited_once_with("catalog")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("distribution", "allowed"), [("ubuntu", True), ("windows-11", True), ("shell-less", False)]
)
async def test_guest_shell_commands_need_the_shell_feature(monkeypatch, distribution, allowed):
    workspace = str(uuid4())
    monkeypatch.setitem(lifecycle._workspace_distributions, workspace, distribution)
    monkeypatch.setattr(lifecycle, "remote_for", AsyncMock(return_value=None))
    local_request = AsyncMock(return_value={"exitCode": 0})
    monkeypatch.setattr(lifecycle, "local_request", local_request)
    if allowed:
        await lifecycle.request("exec", workspace=workspace, arguments=["true"])
        local_request.assert_awaited_once()
    else:
        with pytest.raises(lifecycle.WorkspaceLifecycleError, match="shell-less workspaces yet"):
            await lifecycle.request("exec", workspace=workspace, arguments=["true"])
        local_request.assert_not_called()
        # Lifecycle operations still reach sentineld.
        await lifecycle.request("stop", workspace=workspace)
        local_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_metrics_without_shell_report_state_only(monkeypatch):
    workspace = str(uuid4())
    monkeypatch.setitem(lifecycle._workspace_distributions, workspace, "shell-less")
    monkeypatch.setattr(lifecycle, "remote_for", AsyncMock(return_value=None))
    monkeypatch.setattr(
        lifecycle,
        "statuses",
        AsyncMock(return_value={workspace: {"state": "running", "resources": {"cpus": 2}}}),
    )
    request = AsyncMock(side_effect=AssertionError("no /proc probe without a shell"))
    monkeypatch.setattr(lifecycle, "request", request)
    result = await metrics_module.WorkspaceMetrics().get(workspace)
    assert result == {"state": "running", "resources": {"cpus": 2}}


@pytest.mark.asyncio
async def test_start_always_sends_the_bound_distribution(monkeypatch):
    workspace_id = uuid4()
    monkeypatch.setitem(lifecycle._workspace_distributions, str(workspace_id), "alpine")
    request = AsyncMock(return_value={})
    monkeypatch.setattr(lifecycle, "request", request)
    await lifecycle.start(workspace_id, "/project", ["git"])
    assert request.await_args.kwargs["distribution"] == "alpine"
