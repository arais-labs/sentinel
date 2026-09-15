from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.engine import create_database_engine
from app.dependencies import get_db, get_manager_db
from app.models import Base, Session
from app.models.manager import ManagerBase, Machine
from app.routers.workspaces import router
from app.services.runtime import ssh_runtime
from app.services.runtime.workspace import (
    WorkspaceLocation,
    workspace_paths,
    build_prepare_workspace_script,
    build_delete_session_script,
)
from app.services.sessions.agent_run_registry import AgentRunRegistry


@pytest_asyncio.fixture
async def workspace_app(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.runtime import workspace_containers as containers

    monkeypatch.setattr(containers, "available", lambda: True)
    monkeypatch.setattr(containers, "start", AsyncMock())
    monkeypatch.setattr(containers, "stop", AsyncMock())
    monkeypatch.setattr(containers, "statuses", AsyncMock(return_value={}))
    monkeypatch.setattr(
        containers,
        "overview",
        AsyncMock(
            return_value={
                "states": {},
                "resources": {"cpus": 2, "memory_gib": 2, "disk_gib": 32},
            }
        ),
    )
    instance_engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path}/instance.sqlite")
    manager_engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path}/app.sqlite")
    async with instance_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with manager_engine.begin() as conn:
        await conn.run_sync(ManagerBase.metadata.create_all)
    factory = async_sessionmaker(instance_engine, expire_on_commit=False)
    manager_factory = async_sessionmaker(manager_engine, expire_on_commit=False)
    async with manager_factory() as db:
        machine = Machine(
            name="test Mac",
            provider="local",
            status="ready",
        )
        db.add(machine)
        await db.commit()
    async with factory() as db:
        first, second = Session(user_id="local"), Session(user_id="local")
        db.add_all([first, second])
        await db.commit()
    app = FastAPI()
    app.include_router(router, prefix="/instances/{instance_name}")
    from app.routers.machines import router as machine_router

    app.include_router(machine_router, prefix="/instances/{instance_name}")
    app.state.agent_run_registry = AgentRunRegistry()

    async def session_db():
        async with factory() as db:
            yield db

    async def manager_db():
        async with manager_factory() as db:
            yield db

    app.dependency_overrides[get_db] = session_db
    app.dependency_overrides[get_manager_db] = manager_db
    monkeypatch.setattr(ssh_runtime, "ManagerSessionLocal", manager_factory)
    from app import dependencies

    async def get_factory(name):
        assert name == "main"
        return factory

    monkeypatch.setattr(dependencies, "get_instance_session_factory", get_factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test/instances/main/"
    ) as client:
        yield client, factory, machine, first, second, app.state.agent_run_registry
    await ssh_runtime.close_runtime_terminal_manager()
    await instance_engine.dispose()
    await manager_engine.dispose()


@pytest.mark.asyncio
async def test_workspace_catalog_and_healthy_status_do_not_wait_for_remote(
    workspace_app, monkeypatch
):
    from app.models import Workspace
    from app.services.runtime import workspace_containers as containers

    client, factory, machine, *_ = workspace_app
    async with factory() as db:
        slow = Workspace(name="Slow remote", machine_id=machine.id, directory="/slow")
        healthy = Workspace(name="Healthy", machine_id=machine.id, directory="/healthy")
        db.add_all([slow, healthy])
        await db.commit()
    entered, release = asyncio.Event(), asyncio.Event()

    async def statuses(workspace_id):
        if workspace_id == slow.id:
            entered.set()
            await release.wait()
        return {str(workspace_id): {"state": "running"}}

    monkeypatch.setattr(containers, "statuses", statuses)
    pending = asyncio.create_task(client.get(f"workspaces/{slow.id}/status"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        catalog = await asyncio.wait_for(client.get("workspaces?include_runtime=false"), 1)
        assert catalog.status_code == 200
        assert {row["name"] for row in catalog.json()} == {"Slow remote", "Healthy"}
        assert all(row["container_state"] == "checking" for row in catalog.json())
        containers.overview.assert_not_awaited()
        ready = await asyncio.wait_for(client.get(f"workspaces/{healthy.id}/status"), 1)
        assert ready.json()["container_state"] == "running"
        assert not pending.done()
    finally:
        release.set()
        await pending


@pytest.mark.asyncio
async def test_workspace_status_timeout_is_local_to_its_card(workspace_app, monkeypatch):
    from unittest.mock import AsyncMock
    from app.models import Workspace
    from app.services.runtime import workspace_containers as containers

    client, factory, machine, *_ = workspace_app
    async with factory() as db:
        row = Workspace(name="Remote", machine_id=machine.id, directory="/remote")
        db.add(row)
        await db.commit()
    monkeypatch.setattr(containers, "statuses", AsyncMock(side_effect=TimeoutError))
    response = await client.get(f"workspaces/{row.id}/status")
    assert response.status_code == 200
    assert response.json()["container_state"] == "unavailable"
    assert "timed out" in response.json()["container_error"]
    assert (await client.get(f"workspaces/{uuid4()}/status")).status_code == 404


@pytest.mark.asyncio
async def test_development_tool_plan_is_saved_and_macos_only(workspace_app, monkeypatch):
    from app.services.runtime import development_tools

    client, _, machine, *_ = workspace_app

    async def macos(_):
        return "darwin"

    monkeypatch.setattr(development_tools, "machine_os", macos)
    options = await client.get(f"machines/{machine.id}/development-tools")
    assert options.status_code == 200
    assert options.json()["stacks"] == development_tools.STACKS
    payload = {
        "name": "Full stack",
        "machine_id": str(machine.id),
        "directory": "/projects/full-stack",
        "development_tools": ["node", "python", "git", "git"],
    }
    response = await client.post("workspaces", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["development_tools"] == ["git", "node", "python"]
    assert (await client.get("workspaces")).json()[0]["development_tools"] == [
        "git",
        "node",
        "python",
    ]
    assert (
        await client.post("workspaces", json={**payload, "development_tools": ["unknown"]})
    ).status_code == 422

    async def linux(_):
        return "linux"

    monkeypatch.setattr(development_tools, "machine_os", linux)
    assert (await client.get(f"machines/{machine.id}/development-tools")).json()["stacks"] == []
    monkeypatch.setattr(
        __import__("app.services.runtime.workspace_containers", fromlist=["available"]),
        "available",
        lambda: False,
    )
    assert (await client.post("workspaces", json=payload)).status_code == 422


@pytest.mark.asyncio
async def test_sessions_start_unattached_and_share_workspace(workspace_app, tmp_path):
    client, factory, machine, first, second, registry = workspace_app
    assert (await client.get(f"sessions/{first.id}/workspace")).json() is None
    assert not await ssh_runtime.runtime_configured(
        instance_name="main", session_id=first.id, session_factory=factory
    )
    created = await client.post(
        "workspaces",
        json={
            "name": "Project",
            "machine_id": str(machine.id),
            "directory": str(tmp_path / "project"),
        },
    )
    assert created.status_code == 201, created.text
    workspace = created.json()
    for session in [first, second]:
        result = await client.put(
            f"sessions/{session.id}/workspace", json={"workspace_id": workspace["id"]}
        )
        assert result.status_code == 200, result.text
    first_manager = await ssh_runtime.get_runtime_terminal_manager(
        instance_name="main", session_id=first.id, session_factory=factory
    )
    second_manager = await ssh_runtime.get_runtime_terminal_manager(
        instance_name="main", session_id=second.id, session_factory=factory
    )
    assert first_manager is not second_manager
    a = workspace_paths(str(first.id), root=first_manager.workspace_location)
    b = workspace_paths(str(second.id), root=second_manager.workspace_location)
    assert a.workspace == b.workspace == str(tmp_path / "project")
    assert a.tmux != b.tmux
    assert a.session_root != b.session_root
    assert (await client.delete(f"workspaces/{workspace['id']}")).status_code == 409
    # Running agent cannot change its binding; its registration and the change serialize.
    task = asyncio.create_task(asyncio.sleep(60))
    await registry.register(str(first.id), task)
    result = await client.put(f"sessions/{first.id}/workspace", json={"workspace_id": None})
    assert result.status_code == 409
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await registry.clear(str(first.id), task)
    # Removing conversations leaves the shared directory and registration intact.
    project = tmp_path / "project"
    project.mkdir()
    (project / "keep.txt").write_text("shared project")
    async with factory() as db:
        for session in [first, second]:
            row = await db.get(Session, session.id)
            await db.delete(row)
        await db.commit()
    assert (await client.delete(f"workspaces/{workspace['id']}")).status_code == 204
    assert (project / "keep.txt").read_text() == "shared project"


@pytest.mark.asyncio
async def test_workspace_validation_and_isolation(workspace_app, tmp_path):
    client, factory, machine, first, second, registry = workspace_app
    assert (
        await client.post(
            "workspaces",
            json={
                "name": "Project",
                "machine_id": str(uuid4()),
                "directory": str(tmp_path / "p"),
            },
        )
    ).status_code == 404
    for path in ["relative", "/", "/tmp/../etc", "/tmp/\nfoo"]:
        assert (
            await client.post(
                "workspaces",
                json={
                    "name": "Project",
                    "machine_id": str(machine.id),
                    "directory": path,
                },
            )
        ).status_code == 422
    assert (
        await client.put(f"sessions/{first.id}/workspace", json={"workspace_id": str(uuid4())})
    ).status_code == 404
    async with factory() as db:
        row = await db.get(Session, first.id)
        assert row.workspace_id is None


def test_project_and_private_state_paths_are_separate():
    location = WorkspaceLocation("/projects/shared", "/sentinel/state/sessions")
    paths = workspace_paths(str(uuid4()), root=location)
    assert paths.home == "/root"
    assert paths.tmp == "/tmp"
    _, args = build_prepare_workspace_script(paths.session_id, root=location)
    assert args[0] == paths.workspace
    assert paths.workspace not in args[4:]
    _, args = build_delete_session_script(paths.session_id, root=location)
    assert args == [paths.control_root, paths.session_root]
    assert "/projects/shared" not in args


@pytest.mark.asyncio
async def test_container_sessions_share_files_but_not_shell_state(tmp_path, container_transport):
    from app.services.runtime.terminal_manager import RuntimeTerminalManager

    root = WorkspaceLocation("/workspace", "/var/lib/sentinel")
    first, second = str(uuid4()), str(uuid4())
    one = RuntimeTerminalManager(container_transport, workspace_location=root)
    two = RuntimeTerminalManager(container_transport, workspace_location=root)
    from app.services.runtime.panes import TmuxPanes

    async def execute(manager, session, command, **kwargs):
        from app.schemas.runtime import RuntimeExecResult

        result = await TmuxPanes(manager).execute(session, command, **kwargs)
        return RuntimeExecResult(
            exit_status=result["exit_status"],
            stdout=result["stdout"],
            stderr=result["stderr"],
        )

    try:
        a = await execute(
            one,
            first,
            'printf shared > shared.txt; printf installed > "$HOME/package.txt"; printf cached > "$XDG_CACHE_HOME/build.txt"; printf scratch > "$TMPDIR/scratch.txt"; export SENTINEL_TEST_ONLY=first; printf ready',
            timeout=15,
        )
        assert a.exit_status == 0, a.stderr
        b = await execute(
            two,
            second,
            'cat shared.txt; printf "|%s" "${SENTINEL_TEST_ONLY-unset}"',
            timeout=15,
        )
        assert b.exit_status == 0, b.stderr
        assert "shared|unset" in b.stdout
        shared = await execute(
            two,
            second,
            'cat "$HOME/package.txt" "$XDG_CACHE_HOME/build.txt" "$TMPDIR/scratch.txt"',
            timeout=15,
        )
        assert shared.exit_status == 0 and "installedcachedscratch" in shared.stdout
        finished = asyncio.Event()

        async def completed(job, stdout, stderr):
            assert job["returncode"] == 0
            finished.set()

        await TmuxPanes(one).execute(
            first,
            'printf background; printf persisted > "$HOME/background.txt"',
            background=True,
            on_complete=completed,
            timeout=15,
        )
        await asyncio.wait_for(finished.wait(), 20)

        await one.delete_session_state(first)
        c = await execute(two, second, "cat shared.txt", timeout=15)
        assert c.exit_status == 0 and "shared" in c.stdout
        retained = await execute(
            two, second, 'cat "$HOME/package.txt" "$HOME/background.txt"', timeout=15
        )
        assert retained.exit_status == 0 and "installedpersisted" in retained.stdout
    except Exception as exc:
        pytest.fail(
            f"Local workspace execution failed: {exc}; detail={getattr(exc, 'detail', None)}"
        )
    finally:
        await one.delete_session_state(first)
        await two.delete_session_state(second)
        await one.close()
        await two.close()


@pytest.mark.asyncio
async def test_edit_workspace_adds_tools_and_preserves_location(workspace_app):
    from app.services.runtime import workspace_containers as containers

    client, factory, machine, first, second, registry = workspace_app
    created = await client.post(
        "workspaces",
        json={
            "name": "Tools",
            "machine_id": str(machine.id),
            "directory": "/projects/tools",
            "development_tools": ["git"],
        },
    )
    workspace_id = created.json()["id"]
    containers.start.reset_mock()
    result = await client.patch(
        f"workspaces/{workspace_id}",
        json={
            "name": "More tools",
            "development_tools": ["node", "git"],
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["development_tools"] == ["git", "node"]
    assert result.json()["directory"] == "/projects/tools"
    assert result.json()["container_state"] == "preparing"
    containers.start.assert_awaited_once()
    assert (
        await client.patch(
            f"workspaces/{workspace_id}",
            json={
                "name": "More tools",
                "development_tools": ["git"],
            },
        )
    ).status_code == 422
    containers.statuses.return_value = {workspace_id: {"state": "preparing"}}
    assert (
        await client.patch(
            f"workspaces/{workspace_id}",
            json={
                "name": "More tools",
                "development_tools": ["git", "node", "python"],
            },
        )
    ).status_code == 409


@pytest.mark.asyncio
async def test_workspace_browser_reads_without_a_session_and_never_starts_container(
    workspace_app, monkeypatch
):
    import json
    from unittest.mock import AsyncMock
    from app.models import Workspace
    from app.services.runtime import workspace_containers as containers

    client, factory, machine, *_ = workspace_app
    async with factory() as db:
        workspace = Workspace(name="Browse", machine_id=machine.id, directory="/projects/browse")
        db.add(workspace)
        await db.commit()
        identifier = str(workspace.id)
    response = await client.get(f"workspaces/{identifier}/browse/files")
    assert response.status_code == 409
    containers.start.assert_not_awaited()
    monkeypatch.setattr(
        containers,
        "statuses",
        AsyncMock(return_value={identifier: {"state": "running"}}),
    )
    execute = AsyncMock(
        return_value={
            "stdout": json.dumps({"ok": True, "data": {"entries": [], "session_id": "internal"}}),
            "exitCode": 0,
        }
    )
    monkeypatch.setattr(containers, "request", execute)
    response = await client.get(f"workspaces/{identifier}/browse/files")
    assert response.status_code == 200
    assert response.json() == {"entries": [], "workspace_id": identifier}
    request = json.loads(execute.call_args.kwargs["arguments"][-1])
    assert request["operation"] == "list_files"
    assert request["workspace"] == "/projects/browse"
    response = await client.get(
        f"workspaces/{identifier}/browse/context", params={"include_worktrees": "false"}
    )
    assert response.status_code == 200
    request = json.loads(execute.call_args.kwargs["arguments"][-1])
    assert request["operation"] == "git_context"
    assert request["payload"]["include_worktrees"] is False
    containers.start.assert_not_awaited()
    execute.reset_mock()
    assert (await client.get(f"workspaces/{identifier}/browse/str_replace")).status_code == 422
    execute.assert_not_awaited()
    assert (await client.get(f"workspaces/{uuid4()}/browse/files")).status_code == 404


@pytest.mark.asyncio
async def test_workspace_browser_surfaces_git_failure(workspace_app, monkeypatch):
    import json
    from unittest.mock import AsyncMock
    from app.models import Workspace
    from app.services.runtime import workspace_containers as containers

    client, factory, machine, *_ = workspace_app
    async with factory() as db:
        workspace = Workspace(
            name="Broken worktree", machine_id=machine.id, directory="/projects/broken"
        )
        db.add(workspace)
        await db.commit()
        identifier = str(workspace.id)
    monkeypatch.setattr(
        containers,
        "statuses",
        AsyncMock(return_value={identifier: {"state": "running"}}),
    )
    monkeypatch.setattr(
        containers,
        "request",
        AsyncMock(
            return_value={
                "stdout": json.dumps(
                    {
                        "ok": False,
                        "error": "invalid_path",
                        "detail": "Git metadata unavailable",
                    }
                )
            }
        ),
    )
    response = await client.get(f"workspaces/{identifier}/browse/context")
    assert response.status_code == 422
    assert response.json()["detail"] == "Git metadata unavailable"


@pytest.mark.asyncio
async def test_install_desktop_without_renaming_workspace(workspace_app):
    from app.services.runtime import workspace_containers as containers

    client, _, machine, *_ = workspace_app
    created = await client.post(
        "workspaces",
        json={
            "name": "My project",
            "machine_id": str(machine.id),
            "directory": "/projects/desktop",
            "development_tools": ["git", "node"],
        },
    )
    assert created.status_code == 201, created.text
    identifier = created.json()["id"]
    containers.start.reset_mock()
    # Same partial payload used by the Desktop tab's Install button.
    result = await client.patch(
        f"workspaces/{identifier}",
        json={"development_tools": ["git", "node", "desktop"]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["name"] == "My project"
    assert result.json()["directory"] == "/projects/desktop"
    assert result.json()["development_tools"] == ["desktop", "git", "node"]
    assert result.json()["container_state"] == "preparing"
    from uuid import UUID

    containers.start.assert_awaited_once_with(
        UUID(identifier),
        "/projects/desktop",
        ["desktop", "git", "node"],
        notification_context={"instanceName": "main", "name": "My project"},
    )
    # Retrying the same selection neither renames nor restarts the workspace.
    containers.start.reset_mock()
    retry = await client.patch(
        f"workspaces/{identifier}",
        json={"development_tools": ["desktop", "git", "node"]},
    )
    assert retry.status_code == 200
    containers.start.assert_not_awaited()
    for invalid_name in ["", "   "]:
        assert (
            await client.patch(f"workspaces/{identifier}", json={"name": invalid_name})
        ).status_code == 422


@pytest.mark.asyncio
async def test_provisioning_passes_validated_resources_to_runtime(workspace_app):
    from app.services.runtime import workspace_containers as containers

    client, _, machine, *_ = workspace_app
    allocation = {"cpus": 4, "memory_gib": 8, "disk_gib": 64}
    payload = {
        "name": "Clusters",
        "machine_id": str(machine.id),
        "directory": "/projects/clusters",
        "development_tools": ["kind", "k3s", "docker-builder"],
        "resources": allocation,
    }
    response = await client.post("workspaces", json=payload)
    assert response.status_code == 201, response.text
    assert containers.start.await_args.kwargs == {
        "resources": allocation,
        "notification_context": {"instanceName": "main", "name": "Clusters"},
    }
    for invalid in [
        {**allocation, "cpus": 0},
        {**allocation, "memory_gib": 1.5},
        {**allocation, "disk_gib": 2048},
        {**allocation, "extra": 1},
    ]:
        response = await client.post("workspaces", json={**payload, "resources": invalid})
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_distribution_persisted_immutable_and_compatible(workspace_app):
    from app.services.runtime import workspace_containers as containers

    client, factory, machine, *_ = workspace_app
    for distro in ("ubuntu", "debian"):
        response = await client.post(
            "workspaces",
            json={
                "name": distro,
                "machine_id": str(machine.id),
                "directory": "/project",
                "distribution": distro,
                "development_tools": ["git", "python"],
            },
        )
        assert response.status_code == 201, response.text
        row = response.json()
        assert row["distribution"] == distro
        assert containers._workspace_distributions[row["id"]] == distro
        listing = (await client.get("workspaces?include_runtime=false")).json()
        assert next(item for item in listing if item["id"] == row["id"])["distribution"] == distro
        assert (
            await client.patch(f"workspaces/{row['id']}", json={"distribution": "alpine"})
        ).status_code == 422
        assert (
            await client.patch(
                f"workspaces/{row['id']}",
                json={"development_tools": ["git", "python", "desktop"]},
            )
        ).status_code == 200
    for distro, tools in [
        ("arch", []),
        ("ubuntu", ["unknown"]),
        ("debian", ["unknown"]),
    ]:
        response = await client.post(
            "workspaces",
            json={
                "name": "invalid",
                "machine_id": str(machine.id),
                "directory": "/project",
                "distribution": distro,
                "development_tools": tools,
            },
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_recovery_requires_confirmation_and_rejects_active_agents(
    workspace_app, tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock
    from app.services.runtime import workspace_containers as containers

    client, factory, machine, first, second, registry = workspace_app
    created = await client.post(
        "workspaces",
        json={
            "name": "Recover test",
            "machine_id": str(machine.id),
            "directory": str(tmp_path / "project"),
        },
    )
    assert created.status_code == 201, created.text
    workspace_id = created.json()["id"]
    request = AsyncMock(return_value={"state": "recovering"})
    monkeypatch.setattr(containers, "request", request)
    for payload in [
        {},
        {"confirmed": False},
        {"confirmed": "true"},
        {"confirmed": True, "force": True},
    ]:
        response = await client.post(f"workspaces/{workspace_id}/recover", json=payload)
        assert response.status_code == 422
    request.assert_not_awaited()
    response = await client.put(
        f"sessions/{first.id}/workspace", json={"workspace_id": workspace_id}
    )
    assert response.status_code == 200, response.text
    task = asyncio.create_task(asyncio.sleep(60))
    await registry.register(str(first.id), task)
    try:
        response = await client.post(f"workspaces/{workspace_id}/recover", json={"confirmed": True})
        assert response.status_code == 409, response.text
        request.assert_not_awaited()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await registry.clear(str(first.id), task)
    response = await client.post(f"workspaces/{workspace_id}/recover", json={"confirmed": True})
    assert response.status_code == 202, response.text
    request.assert_awaited_once_with("recover", workspace=workspace_id)
