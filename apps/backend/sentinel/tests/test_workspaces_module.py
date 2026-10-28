"""The agent's workspaces tool: the routes' operations, under the module's permissions."""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest

import app.database as database_module
from app.dependencies import get_manager_db
from app.models import Session, Workspace
from app.services.modules.builtins.workspaces import MODULE
from app.services.modules.builtins.workspaces import handlers
from app.services.workspaces import operations, reports
from app.services.modules.builtins.workspaces.module import FIELDS
from app.services.modules.permissions import combined_agent_permissions
from app.services.modules.runtime_services import configure_runtime_services, reset_runtime_services
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolApprovalDecision, ToolRuntimeContext
from app.services.tools.registry_builder import build_default_registry
from app.services.workspaces import lifecycle
from sentral.errors import ToolValidationError
from tests import test_workspaces

workspace_app = test_workspaces.workspace_app

PERMISSIONS = {
    "list": "allow",
    "catalog": "allow",
    "usage": "allow",
    "help": "allow",
    "distribution_read": "allow",
    "source": "allow",
    "log": "allow",
    "create": "approval",
    "start": "approval",
    "stop": "approval",
    "update": "approval",
    "reinstall": "approval",
    "recover": "approval",
    "attach": "approval",
    "distribution": "approval",
    "delete": "deny",
    "distribution_delete": "deny",
}


@pytest.fixture
def agent(workspace_app, monkeypatch):
    """A running agent in the fixture's first chat, seen by the tool as the app sees it."""
    client, factory, machine, first, second, registry = workspace_app
    ws_manager = SimpleNamespace(broadcast=AsyncMock())
    reset_runtime_services()
    configure_runtime_services(
        app_state=SimpleNamespace(agent_run_registry=registry), ws_manager=ws_manager
    )
    manager_db = client._transport.app.dependency_overrides[get_manager_db]
    monkeypatch.setattr(database_module, "ManagerSessionLocal", asynccontextmanager(manager_db))
    # No guest behind the fixture's workspaces: nothing to probe or tear down.
    monkeypatch.setattr(operations, "workspace_attached", AsyncMock(return_value=False))
    monkeypatch.setattr(operations, "invalidate_session_managers", AsyncMock())
    monkeypatch.setattr(operations, "get_browser_pool", lambda: SimpleNamespace(remove=AsyncMock()))
    # The [Workspace Report] watcher has tests of its own; here, only what registers it.
    watch = Mock(return_value=True)
    monkeypatch.setattr(reports, "watch", watch)
    runtime = ToolRuntimeContext(
        session_id=first.id, instance_name="main", db_session_factory=factory
    )
    yield SimpleNamespace(
        watch=watch,
        runtime=runtime,
        client=client,
        factory=factory,
        machine=machine,
        chat=first,
        other=second,
        registry=registry,
        ws=ws_manager,
    )
    reset_runtime_services()


def test_permission_defaults_inspect_allow_change_asks_delete_denies():
    assert {action.id: action.default_permission() for action in MODULE.actions} == PERMISSIONS
    # Seeded on startup, so the settings screen starts from the same levels.
    seeded = combined_agent_permissions()
    assert {key: seeded[f"workspaces.{key}"] for key in PERMISSIONS} == PERMISSIONS


@pytest.mark.asyncio
async def test_the_grouped_tool_applies_each_actions_permission():
    tool = build_default_registry().get("workspaces")
    expected = {
        "allow": ToolApprovalDecision.ALLOW,
        "approval": ToolApprovalDecision.REQUIRE,
        "deny": ToolApprovalDecision.DENY,
    }
    for action, level in PERMISSIONS.items():
        evaluation = await tool.approval_check({"action": action}, ToolRuntimeContext())
        assert evaluation.decision == expected[level], action


def test_action_schemas_are_strict():
    for action in MODULE.actions:
        schema = action.parameters_schema
        assert schema["additionalProperties"] is False, action.id
        assert set(schema["required"]) <= set(schema["properties"]), action.id
    assert MODULE.grouped_tool and MODULE.system


def test_sentineld_still_has_the_ops_this_module_relays():
    ctl = Path(__file__).resolve().parents[4] / "packages/sentineld/core/ctl.cpp"
    table = ctl.read_text().split("kOps[] = {", 1)[1].split("\n};", 1)[0]
    names = set(re.findall(r'^\s*\{"([a-z_]+)",', table, re.MULTILINE))
    assert {
        "catalog",
        "distribution",
        "distribution_read",
        "distribution_delete",
        "help",
        "source",
        "log",
        "usage",
    } <= names


@pytest.mark.asyncio
async def test_agent_creates_and_lists_through_the_routes_operations(agent):
    created = await handlers.handle_create(
        {"name": "Agent box", "machine_id": str(agent.machine.id), "directory": "/projects/a"},
        agent.runtime,
    )
    # The route's defaults and its start, with the agent's chat instance for notifications.
    assert created["state"] == "preparing"
    assert (created["distribution"], created["desktop"]) == ("alpine", "none")
    assert created["resources"] == {"cpus": 2, "memory_gib": 2, "disk_gib": 32}
    assert lifecycle.start.await_args.kwargs["notification_context"] == {
        "instanceName": "main",
        "name": "Agent box",
    }
    # The user's create rejects the same duplicate.
    duplicate = {"name": "Agent box", "machine_id": str(agent.machine.id), "directory": "/x"}
    with pytest.raises(ToolValidationError, match="already exists"):
        await handlers.handle_create(duplicate, agent.runtime)
    assert (await agent.client.post("workspaces", json=duplicate)).status_code == 409
    with pytest.raises(ToolValidationError, match="directory"):
        await handlers.handle_create(
            {**duplicate, "name": "Y", "directory": "relative"}, agent.runtime
        )

    listed = await handlers.handle_list({}, agent.runtime)
    assert [row["name"] for row in listed["workspaces"]] == ["Agent box"]
    assert listed["attached_workspace_id"] is None
    assert listed["machines"] == [
        {
            "id": str(agent.machine.id),
            "name": "test Mac",
            "provider": "local",
            "status": "ready",
            "workspaces_available": True,
        }
    ]


@pytest.mark.asyncio
async def test_agent_attaches_its_own_running_chat_but_the_user_waits(agent):
    created = await handlers.handle_create(
        {"name": "Box", "machine_id": str(agent.machine.id), "directory": "/projects/a"},
        agent.runtime,
    )
    # The agent calling the tool is running; that run is not "another agent".
    task = asyncio.create_task(asyncio.sleep(60))
    await agent.registry.register(str(agent.chat.id), task)
    try:
        attached = await handlers.handle_attach({"workspace_id": created["id"]}, agent.runtime)
        assert attached == {"attached_workspace_id": created["id"]}
        async with agent.factory() as db:
            assert str((await db.get(Session, agent.chat.id)).workspace_id) == created["id"]
        agent.ws.broadcast.assert_awaited_with(
            str(agent.chat.id),
            {
                "type": "workspace_changed",
                "session_id": str(agent.chat.id),
                "workspace_id": created["id"],
            },
        )
        listed = await handlers.handle_list({}, agent.runtime)
        assert listed["attached_workspace_id"] == created["id"]
        # The user's Attach, on the same operation, waits for that agent.
        response = await agent.client.put(
            f"sessions/{agent.chat.id}/workspace", json={"workspace_id": None}
        )
        assert response.status_code == 409
        assert await handlers.handle_attach({"detach": True}, agent.runtime) == {
            "attached_workspace_id": None
        }
        with pytest.raises(ToolValidationError, match="either workspace_id"):
            await handlers.handle_attach({}, agent.runtime)
        with pytest.raises(ToolValidationError, match="Workspace not found"):
            await handlers.handle_attach({"workspace_id": str(uuid4())}, agent.runtime)
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_a_sub_agent_cannot_change_its_parents_workspace(agent):
    async with agent.factory() as db:
        child = Session(user_id="local", parent_session_id=agent.chat.id)
        db.add(child)
        await db.commit()
    runtime = ToolRuntimeContext(
        session_id=child.id,
        runtime_session_id=agent.chat.id,
        instance_name="main",
        db_session_factory=agent.factory,
    )
    with pytest.raises(ToolValidationError, match="parent session's workspace"):
        await handlers.handle_attach({"detach": True}, runtime)


@pytest.mark.asyncio
async def test_start_stop_update_and_delete_are_the_routes_operations(agent):
    created = await handlers.handle_create(
        {"name": "Box", "machine_id": str(agent.machine.id), "directory": "/projects/a"},
        agent.runtime,
    )
    workspace = {"workspace_id": created["id"]}
    lifecycle.start.reset_mock()
    assert (await handlers.handle_start(workspace, agent.runtime))["state"] == "preparing"
    lifecycle.start.assert_awaited_once()
    assert await handlers.handle_stop(workspace, agent.runtime) == {
        "workspace_id": created["id"],
        "state": "stopped",
    }
    lifecycle.stop.assert_awaited_once_with(UUID(created["id"]))
    updated = await handlers.handle_update({**workspace, "name": "Renamed"}, agent.runtime)
    assert updated["name"] == "Renamed"
    with pytest.raises(ToolValidationError, match="cannot change distribution"):
        await handlers.handle_update({**workspace, "distribution": "debian"}, agent.runtime)
    with pytest.raises(ToolValidationError, match="Workspace not found"):
        await handlers.handle_stop({"workspace_id": str(uuid4())}, agent.runtime)

    # Deleting detaches the chats using it, only once the user agreed.
    async with agent.factory() as db:
        (await db.get(Session, agent.other.id)).workspace_id = UUID(created["id"])
        await db.commit()
    with pytest.raises(ToolValidationError, match="would be detached"):
        await handlers.handle_delete(workspace, agent.runtime)
    lifecycle.stop.reset_mock()
    deleted = await handlers.handle_delete({**workspace, "detach_sessions": True}, agent.runtime)
    assert deleted["deleted_workspace_id"] == created["id"]
    lifecycle.stop.assert_awaited_once_with(UUID(created["id"]), delete=True)
    async with agent.factory() as db:
        assert await db.get(Workspace, UUID(created["id"])) is None
        assert (await db.get(Session, agent.other.id)).workspace_id is None


@pytest.mark.asyncio
async def test_preparations_the_agent_starts_are_watched_for_its_report(agent):
    created = await handlers.handle_create(
        {"name": "Box", "machine_id": str(agent.machine.id), "directory": "/projects/a"},
        agent.runtime,
    )
    expected = {
        "session_id": agent.chat.id,
        "workspace_id": UUID(created["id"]),
        "name": "Box",
        "db_factory": agent.factory,
        "run_registry": agent.registry,
    }
    agent.watch.assert_called_once_with(**expected)
    workspace = {"workspace_id": created["id"]}
    agent.watch.reset_mock()
    await handlers.handle_start(workspace, agent.runtime)
    agent.watch.assert_called_once_with(**expected)
    # A rename prepares nothing; a new desktop prepares the workspace again.
    agent.watch.reset_mock()
    await handlers.handle_update({**workspace, "name": "Renamed"}, agent.runtime)
    agent.watch.assert_not_called()
    updated = await handlers.handle_update({**workspace, "desktop": "xfce"}, agent.runtime)
    assert updated["state"] == "preparing"
    agent.watch.assert_called_once_with(**{**expected, "name": "Renamed"})
    # The user's own buttons are not the agent's preparations.
    agent.watch.reset_mock()
    user = {"name": "Mine", "machine_id": str(agent.machine.id), "directory": "/projects/b"}
    assert (await agent.client.post("workspaces", json=user)).status_code == 201
    assert (await agent.client.post(f"workspaces/{created['id']}/start")).status_code == 200
    agent.watch.assert_not_called()
    # A preparation that failed at once is in the reply itself.
    lifecycle.start.side_effect = lifecycle.WorkspaceLifecycleError("no runtime")
    failed = await handlers.handle_create(
        {"name": "Broken", "machine_id": str(agent.machine.id), "directory": "/projects/c"},
        agent.runtime,
    )
    assert failed["state"] == "failed"
    agent.watch.assert_not_called()


@pytest.mark.asyncio
async def test_reinstall_and_recover_are_the_routes_operations_and_report(agent, monkeypatch):
    created = await handlers.handle_create(
        {"name": "Box", "machine_id": str(agent.machine.id), "directory": "/projects/a"},
        agent.runtime,
    )
    workspace = {"workspace_id": created["id"]}
    reinstall = AsyncMock()
    monkeypatch.setattr(lifecycle, "reinstall", reinstall)
    shared = AsyncMock(wraps=operations.reinstall_workspace)
    monkeypatch.setattr(operations, "reinstall_workspace", shared)
    agent.watch.reset_mock()
    # The retry after a failed install: the same workspace, its disk erased, the approval being
    # the user's confirmation.
    reinstalled = await handlers.handle_reinstall({**workspace, "desktop": "xfce"}, agent.runtime)
    assert (reinstalled["id"], reinstalled["state"]) == (created["id"], "preparing")
    assert reinstalled["desktop"] == "xfce"
    payload = shared.await_args.args[4]
    assert payload.confirmed and payload.settings.desktop == "xfce"
    assert reinstall.await_args.kwargs["desktop"] == "xfce"
    agent.watch.assert_called_once()
    assert agent.watch.call_args.kwargs["workspace_id"] == UUID(created["id"])
    # Without fields it reinstalls the workspace as it is.
    await handlers.handle_reinstall(workspace, agent.runtime)
    assert shared.await_args.args[4].settings is None
    lifecycle.statuses.return_value = {created["id"]: {"state": "preparing"}}
    with pytest.raises(ToolValidationError, match="Wait for workspace setup"):
        await handlers.handle_reinstall(workspace, agent.runtime)
    lifecycle.statuses.return_value = {}

    request = AsyncMock(return_value={"state": "recovering"})
    monkeypatch.setattr(lifecycle, "request", request)
    recover = AsyncMock(wraps=operations.recover_workspace)
    monkeypatch.setattr(operations, "recover_workspace", recover)
    agent.watch.reset_mock()
    recovered = await handlers.handle_recover(workspace, agent.runtime)
    assert recovered == {"workspace_id": created["id"], "state": "recovering"}
    recover.assert_awaited_once()
    request.assert_awaited_once_with("recover", workspace=created["id"])
    agent.watch.assert_called_once()
    assert agent.watch.call_args.kwargs["name"] == "Box"
    with pytest.raises(ToolValidationError, match="Workspace not found"):
        await handlers.handle_recover({"workspace_id": str(uuid4())}, agent.runtime)


@pytest.mark.asyncio
async def test_help_and_distributions_reach_the_machines_sentineld(agent, monkeypatch):
    sent = []

    async def local_request(action, **values):
        sent.append((action, values))
        if action == "help":
            return {"text": "Writing a distribution"}
        if action == "distribution_read":
            if "path" in values:
                return {"path": values["path"], "text": "{}", "executable": False}
            return {"files": [{"path": "distribution.json", "bytes": 2}]}
        return {"problem": None}

    monkeypatch.setattr(lifecycle, "local_request", local_request)
    machine = {"machine_id": str(agent.machine.id)}
    assert await handlers.handle_help(machine, agent.runtime) == {"text": "Writing a distribution"}
    await handlers.handle_help({**machine, "topic": "overview"}, agent.runtime)
    files = {"distribution.json": {"text": "{}"}, "old.sh": None}
    lifecycle._catalogs[""] = {"distributions": []}
    await handlers.handle_distribution(
        {**machine, "distribution": "mine", "files": files}, agent.runtime
    )
    # A write changes the catalog: the next read asks sentineld again.
    assert "" not in lifecycle._catalogs
    await handlers.handle_distribution_read(
        {**machine, "distribution": "mine", "paths": ["distribution.json"]}, agent.runtime
    )
    await handlers.handle_distribution_delete({**machine, "distribution": "mine"}, agent.runtime)
    await handlers.handle_source({**machine, "url": "https://example.org/a.tar"}, agent.runtime)
    assert sent == [
        ("help", {"topic": "distributions"}),
        ("help", {}),
        ("distribution", {"distribution": "mine", "files": files}),
        ("distribution_read", {"distribution": "mine"}),
        ("distribution_read", {"distribution": "mine", "path": "distribution.json"}),
        ("distribution_delete", {"distribution": "mine"}),
        ("source", {"url": "https://example.org/a.tar"}),
    ]
    with pytest.raises(ToolValidationError, match="Machine not found"):
        await handlers.handle_help({"machine_id": str(uuid4())}, agent.runtime)


@pytest.mark.asyncio
async def test_source_browses_an_archive_within_one_reply(agent, monkeypatch):
    sent = []

    async def local_request(action, **values):
        sent.append((action, values))
        return {"members": [], "matched": 0}

    monkeypatch.setattr(lifecycle, "local_request", local_request)
    monkeypatch.setattr(operations.settings, "tool_result_max_chars", 1234)
    machine = {"machine_id": str(agent.machine.id), "url": "https://example.org/r.tar.gz"}
    await handlers.handle_source(machine, agent.runtime)
    await handlers.handle_source({**machine, "match": "boot/*"}, agent.runtime)
    await handlers.handle_source(
        {**machine, "paths": ["etc/os-release"], "offset": 5, "length": 9}, agent.runtime
    )
    url = machine["url"]
    assert sent == [
        ("source", {"url": url}),
        ("source", {"url": url, "match": "boot/*", "limit": 1234}),
        (
            "source",
            {"url": url, "paths": ["etc/os-release"], "offset": 5, "length": 9, "limit": 1234},
        ),
    ]


@pytest.mark.asyncio
async def test_usage_reaches_the_machines_sentineld(agent, monkeypatch):
    async def local_request(action, **values):
        assert (action, values) == ("usage", {})
        return {"workspaces": {"unknown": 4}, "shared": {"systems": 8}, "total": 12, "free": 1}

    monkeypatch.setattr(lifecycle, "local_request", local_request)
    usage = await handlers.handle_usage({"machine_id": str(agent.machine.id)}, agent.runtime)
    assert (usage["other"], usage["shared"]["systems"], usage["total"]) == (4, 8, 12)
    assert (usage["free"], usage["capacity"]) == (1, None)
    with pytest.raises(ToolValidationError, match="Machine not found"):
        await handlers.handle_usage({"machine_id": str(uuid4())}, agent.runtime)


@pytest.mark.asyncio
async def test_sentineld_errors_reach_the_agent(agent, monkeypatch):
    async def local_request(action, **values):
        raise lifecycle.WorkspaceLifecycleError("distribution alpine is bundled")

    monkeypatch.setattr(lifecycle, "local_request", local_request)
    with pytest.raises(ToolValidationError, match="is bundled"):
        await handlers.handle_distribution(
            {
                "machine_id": str(agent.machine.id),
                "distribution": "alpine",
                "files": {"a": {"text": ""}},
            },
            agent.runtime,
        )


@pytest.mark.asyncio
async def test_list_runs_through_the_tool_executor(agent):
    result, _ = await ToolExecutor(build_default_registry()).execute(
        "workspaces", {"action": "list"}, runtime=agent.runtime
    )
    assert result["workspaces"] == [] and len(result["machines"]) == 1
    with pytest.raises(ToolValidationError, match="Unknown field"):
        await ToolExecutor(build_default_registry()).execute(
            "workspaces", {"action": "list", "machine_id": "x"}, runtime=agent.runtime
        )


@pytest.mark.asyncio
async def test_distribution_read_lists_then_reads_whole_files(agent, monkeypatch):
    files = {"distribution.json": "{}", "install/setup.sh": "#!/bin/sh", "big.iso": "x" * 900}
    reads = []

    async def local_request(action, **values):
        reads.append(values.get("path"))
        if "path" not in values:
            listed = [{"path": p, "bytes": len(t)} for p, t in files.items()]
            return {"distribution": "debian", "files": listed, "problem": None}
        return {"path": values["path"], "text": files[values["path"]], "executable": True}

    monkeypatch.setattr(lifecycle, "local_request", local_request)
    monkeypatch.setattr(operations.settings, "tool_result_max_chars", 500)
    machine = {"machine_id": str(agent.machine.id), "distribution": "debian"}
    # Without paths: the listing, sizes and no contents.
    listing = await handlers.handle_distribution_read(machine, agent.runtime)
    assert {f["path"]: f["bytes"] for f in listing["files"]}["big.iso"] == 900
    # With paths: several whole files in one call.
    read = await handlers.handle_distribution_read(
        {**machine, "paths": ["distribution.json", "install/setup.sh"]}, agent.runtime
    )
    assert read["files"] == {
        "distribution.json": {"text": "{}", "executable": True},
        "install/setup.sh": {"text": "#!/bin/sh", "executable": True},
    }
    # Over what one reply holds: refused with the sizes, nothing partial.
    reads.clear()
    with pytest.raises(ToolValidationError, match="Read fewer at a time.*big.iso"):
        await handlers.handle_distribution_read(
            {**machine, "paths": ["big.iso", "distribution.json"]}, agent.runtime
        )
    assert reads == [None]
    with pytest.raises(ToolValidationError, match="No such file in debian: nope"):
        await handlers.handle_distribution_read({**machine, "paths": ["nope"]}, agent.runtime)


@pytest.mark.asyncio
async def test_log_asks_the_workspaces_own_runtime(agent, monkeypatch):
    created = await handlers.handle_create(
        {"name": "Box", "machine_id": str(agent.machine.id), "directory": "/projects/a"},
        agent.runtime,
    )
    sent = []

    async def request(action, **values):
        sent.append((action, values))
        return {"console": "Kernel panic", "build": None, "error": "boot failed"}

    monkeypatch.setattr(lifecycle, "request", request)
    log = await handlers.handle_log({"workspace_id": created["id"], "lines": 50}, agent.runtime)
    assert log["console"] == "Kernel panic"
    await handlers.handle_log({"workspace_id": created["id"]}, agent.runtime)
    assert sent == [
        ("log", {"workspace": created["id"], "lines": 50}),
        ("log", {"workspace": created["id"]}),
    ]
    with pytest.raises(ToolValidationError, match="Workspace not found"):
        await handlers.handle_log({"workspace_id": str(uuid4())}, agent.runtime)


def test_source_accepts_plain_http():
    pattern = re.compile(FIELDS["url"]["pattern"])
    assert pattern.match("http://example.org/a.tar") and pattern.match("https://example.org/a")
    assert not pattern.match("ftp://example.org/a")
