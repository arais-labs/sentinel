from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.services.runtime.remote_mac import RemoteMacError, UpdateApprovalRequired
from app.services.runtime.remote_update import apply_update

OLD = {"version": "old", "storeVersion": 1, "updateProtocol": 1}
NEW = {"version": "new", "storeVersion": 1, "updateProtocol": 1}


class MigrationSession:
    def __init__(self, *, preflight_error=False, migration_error=False):
        self.state = {
            "manifest": deepcopy(OLD),
            "owner_free": False,
            "journal": None,
            "migrations": [],
        }
        self.events = []
        self.preflight_error = preflight_error
        self.migration_error = migration_error

    async def request(self, action, **values):
        self.events.append((action, deepcopy(values)))
        if action == "inspect":
            return deepcopy(self.state)
        if action == "migration_plan" and self.preflight_error:
            raise RemoteMacError("Missing workspace registration")
        if action == "journal":
            self.state["journal"] = deepcopy(values["journal"])
        if action == "migrate":
            assert self.state["owner_free"]
            if self.migration_error:
                raise RemoteMacError("Migration failed")
            self.state["migrations"] = ["001_worker_ownership"]
        if action == "activate":
            assert self.state["owner_free"]
            assert values["manifest"]["runtimeMigrations"] == self.state["migrations"]
            self.state["manifest"] = deepcopy(values["manifest"])
            self.state["owner_free"] = False


@pytest.mark.asyncio
async def test_migration_preflight_failure_never_stops_the_old_service():
    session = MigrationSession(preflight_error=True)
    cb = callbacks(session, ["workspace"])
    with pytest.raises(RemoteMacError, match="Missing workspace"):
        await apply_update(
            session,
            {**NEW, "runtimeMigrations": ["001_worker_ownership"]},
            migration_inputs={},
            **cb,
        )
    cb["stop"].assert_not_called()
    assert session.state["manifest"] == OLD
    assert session.state["journal"] is None


@pytest.mark.asyncio
async def test_migrations_run_under_ownership_before_activation_and_resume():
    session = MigrationSession()
    cb = callbacks(session, ["workspace"])
    await apply_update(
        session, {**NEW, "runtimeMigrations": ["001_worker_ownership"]}, migration_inputs={}, **cb
    )
    actions = [action for action, _ in session.events]
    assert actions.index("migration_plan") < actions.index("migrate") < actions.index("activate")
    cb["stop"].assert_awaited_once()
    cb["resume"].assert_awaited_once_with(["workspace"])


@pytest.mark.asyncio
async def test_failed_migration_does_not_activate_or_resume():
    session = MigrationSession(migration_error=True)
    cb = callbacks(session, ["workspace"])
    with pytest.raises(RemoteMacError, match="Migration failed"):
        await apply_update(
            session,
            {**NEW, "runtimeMigrations": ["001_worker_ownership"]},
            migration_inputs={},
            **cb,
        )
    assert not any(action == "activate" for action, _ in session.events)
    cb["resume"].assert_not_called()
    assert session.state["journal"]["phase"] == "migrating"


@pytest.mark.asyncio
async def test_failed_activation_after_migration_never_rolls_back_to_incompatible_runtime():
    session = MigrationSession()
    cb = callbacks(session, ["workspace"])
    cb["validate"].side_effect = RemoteMacError("New runtime failed")
    with pytest.raises(RemoteMacError, match="needs recovery"):
        await apply_update(
            session,
            {**NEW, "runtimeMigrations": ["001_worker_ownership"]},
            migration_inputs={},
            **cb,
        )
    assert [
        values["manifest"]["version"] for action, values in session.events if action == "activate"
    ] == ["new"]
    cb["resume"].assert_not_called()
    assert session.state["journal"]["phase"] == "recovery_required"


class Session:
    def __init__(self, current=OLD, live=False, journal=None):
        self.state = {
            "manifest": deepcopy(current),
            "owner_free": not live,
            "journal": journal,
        }
        self.events = []
        self.fail_activation = False

    async def request(self, action, **values):
        self.events.append((action, deepcopy(values)))
        if action == "inspect":
            return deepcopy(self.state)
        if action == "journal":
            self.state["journal"] = deepcopy(values["journal"])
        if action == "activate":
            assert self.state["owner_free"], "Cannot replace an existing owner"
            self.state["manifest"] = deepcopy(values["manifest"])
            self.state["owner_free"] = self.fail_activation


def callbacks(session, running=()):
    async def stop(current, approved):
        assert set(running).issubset(approved)
        session.state["owner_free"] = True

    return dict(
        snapshot=AsyncMock(return_value={"states": {id: "running" for id in running}}),
        stop=AsyncMock(side_effect=stop),
        validate=AsyncMock(),
        resume=AsyncMock(),
        progress=lambda phase: None,
        approved=list(running),
        owner_timeout=0.1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("current", [None, {"version": "broken-legacy"}, OLD])
async def test_dead_runtime_updates_without_starting_or_contacting_old_helper(current):
    session = Session(current)
    cb = callbacks(session)
    await apply_update(session, NEW, **cb)
    cb["snapshot"].assert_not_called()
    cb["stop"].assert_not_called()
    cb["validate"].assert_awaited_once_with(NEW)
    assert session.state["manifest"] == NEW
    assert session.state["journal"]["phase"] == "complete"


@pytest.mark.asyncio
async def test_healthy_runtime_requires_approval_before_stopping():
    session = Session(live=True)
    cb = callbacks(session, ["workspace"])
    cb["approved"] = []
    with pytest.raises(UpdateApprovalRequired):
        await apply_update(session, NEW, **cb)
    cb["stop"].assert_not_called()
    assert session.state["manifest"] == OLD
    assert session.state["journal"] is None


@pytest.mark.asyncio
async def test_same_version_reinstall_requires_approval_and_replaces_release():
    session = Session(NEW, live=True)
    cb = callbacks(session, ["workspace"])
    cb["approved"] = []
    with pytest.raises(UpdateApprovalRequired):
        await apply_update(session, NEW, reinstall=True, **cb)
    cb["stop"].assert_not_called()
    assert not any(action == "activate" for action, _ in session.events)

    cb["approved"] = ["workspace"]
    await apply_update(session, NEW, reinstall=True, **cb)
    cb["stop"].assert_awaited_once_with(NEW, ["workspace"])
    cb["resume"].assert_awaited_once_with(["workspace"])
    assert any(action == "activate" for action, _ in session.events)


@pytest.mark.asyncio
async def test_reinstall_recovers_interrupted_activation_without_replaying_shutdown():
    session = Session(NEW, live=True, journal={"phase": "resuming", "previous": OLD})
    cb = callbacks(session, ["workspace"])
    await apply_update(session, NEW, reinstall=True, **cb)
    cb["stop"].assert_not_called()
    cb["resume"].assert_not_called()
    assert not any(action == "activate" for action, _ in session.events)
    assert "recovered" in session.state["journal"]["warning"]


@pytest.mark.asyncio
async def test_healthy_runtime_stops_validates_and_resumes_approved_workspaces():
    session = Session(live=True)
    cb = callbacks(session, ["workspace"])
    await apply_update(session, NEW, **cb)
    cb["stop"].assert_awaited_once_with(OLD, ["workspace"])
    cb["resume"].assert_awaited_once_with(["workspace"])
    phases = [value["journal"]["phase"] for action, value in session.events if action == "journal"]
    assert phases == [
        "staged",
        "stopping",
        "activating",
        "initializing",
        "resuming",
        "complete",
    ]


@pytest.mark.asyncio
async def test_held_owner_lock_with_dead_socket_is_not_treated_as_dead_service():
    session = Session(live=True)
    cb = callbacks(session)
    cb["snapshot"].side_effect = RemoteMacError("not responding")
    with pytest.raises(RemoteMacError, match="not responding"):
        await apply_update(session, NEW, **cb)
    cb["stop"].assert_not_called()
    assert session.state["manifest"] == OLD


@pytest.mark.asyncio
async def test_does_not_activate_until_owner_lock_is_released():
    session = Session(live=True)
    cb = callbacks(session)
    cb["stop"] = AsyncMock()  # Shutdown acknowledgement but lock remains held.
    with pytest.raises(RemoteMacError, match="still owns"):
        await apply_update(session, NEW, **cb)
    assert session.state["manifest"] == OLD


@pytest.mark.asyncio
async def test_failed_start_rolls_back_compatible_release_before_resuming_work():
    session = Session()
    cb = callbacks(session)
    cb["validate"].side_effect = [RemoteMacError("failed boot"), None]
    awaitable = apply_update(session, NEW, **cb)
    with pytest.raises(RemoteMacError, match="previous runtime restored"):
        await awaitable
    assert session.state["manifest"] == OLD
    assert session.state["journal"]["phase"] == "rolled_back"
    cb["resume"].assert_not_called()


@pytest.mark.asyncio
async def test_failed_legacy_upgrade_preserves_recovery_state_without_unsafe_rollback():
    session = Session({"version": "broken-legacy"})
    cb = callbacks(session)
    cb["validate"].side_effect = RemoteMacError("failed boot")
    with pytest.raises(RemoteMacError, match="needs recovery"):
        await apply_update(session, NEW, **cb)
    assert session.state["journal"]["previous"] == {"version": "broken-legacy"}
    assert session.state["journal"]["phase"] == "recovery_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["activating", "initializing", "resuming"])
async def test_disconnect_after_activation_reconciles_without_replaying_mutations(
    phase,
):
    session = Session(
        NEW,
        live=True,
        journal={"phase": phase, "previous": OLD, "running": ["workspace"]},
    )
    cb = callbacks(session)
    await apply_update(session, NEW, **cb)
    cb["stop"].assert_not_called()
    cb["resume"].assert_not_called()
    assert not any(action == "activate" for action, _ in session.events)
    assert session.state["journal"]["phase"] == "complete"


@pytest.mark.asyncio
async def test_interrupted_shutdown_recovers_dead_service_without_replaying_workspaces():
    session = Session(OLD, journal={"phase": "stopping", "previous": OLD, "running": ["workspace"]})
    cb = callbacks(session)
    await apply_update(session, NEW, **cb)
    cb["resume"].assert_not_called()
    assert "not replayed" in session.state["journal"]["warning"]


@pytest.mark.asyncio
async def test_resume_failure_keeps_new_runtime_no_rollback():
    session = Session(live=True)
    cb = callbacks(session, ["workspace"])
    cb["resume"].side_effect = RemoteMacError("workspace failed")
    with pytest.raises(RemoteMacError, match="retry affected workspaces"):
        await apply_update(session, NEW, **cb)
    assert session.state["manifest"] == NEW
    assert session.state["journal"]["phase"] == "complete"


@pytest.mark.asyncio
async def test_storage_migration_is_not_implicitly_authorized():
    session = Session()
    with pytest.raises(RemoteMacError, match="storage migration"):
        await apply_update(session, {**NEW, "storeVersion": 2}, **callbacks(session))
    assert session.state["manifest"] == OLD


@pytest.mark.asyncio
@pytest.mark.parametrize("updating", [False, True])
async def test_only_update_owner_can_attach_during_transaction(monkeypatch, updating):
    import asyncio
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import Mock
    from tests.test_remote_mac import machine
    from app.services.runtime import remote_mac as remote, workspace_containers

    target = machine(host_key="key", runtime_root="/remote/runtime")
    runtime = remote.RemoteMacRuntime(target)

    class SFTP:
        @asynccontextmanager
        async def open(self, name):
            value = (
                {"phase": "resuming"}
                if name.endswith("update.json")
                else {
                    "executable": "/new/helper",
                    "kernel": "/kernel",
                    "initImage": "init",
                    "workspaceImage": "workspace",
                }
            )
            yield SimpleNamespace(read=AsyncMock(return_value=json.dumps(value)))

    @asynccontextmanager
    async def sftp():
        yield SFTP()

    conn = SimpleNamespace(
        start_sftp_client=sftp, forward_local_path=AsyncMock(return_value=Mock())
    )
    runtime.ssh = SimpleNamespace(
        _conn=None, close=AsyncMock(), _ensure_conn=AsyncMock(return_value=conn)
    )
    status = AsyncMock(return_value={"states": {}})
    monkeypatch.setattr(remote, "service_request", status)
    monkeypatch.setattr(remote, "wait_for_runtime_ready", AsyncMock())
    monkeypatch.setattr(
        workspace_containers,
        "local_request",
        AsyncMock(return_value={"socket": "/bridge"}),
    )
    if updating:
        runtime.maintenance_owner = asyncio.current_task()
        monkeypatch.setitem(remote._maintenance, str(target.id), runtime.maintenance_owner)
    try:
        if updating:
            await runtime.connect()
            status.assert_awaited_once()
        else:
            with pytest.raises(RemoteMacError, match="pending recovery"):
                await runtime.connect()
            status.assert_not_called()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_update_lease_conflict_closes_probe_without_sending_mutations():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.services.runtime.remote_update import UpdateSession

    process = SimpleNamespace(
        stdout=SimpleNamespace(readline=AsyncMock(return_value='{"error":"Runtime is busy"}\n')),
        stdin=Mock(),
        close=Mock(),
    )
    conn = SimpleNamespace(create_process=AsyncMock(return_value=process))
    with pytest.raises(RemoteMacError, match="busy"):
        async with UpdateSession(conn, "/new/helper", "/root"):
            pytest.fail("Must not enter a competing transaction")
    process.stdin.write.assert_not_called()
    process.close.assert_called_once()


@pytest.mark.asyncio
async def test_lost_update_reply_is_not_retried_or_masked_by_cleanup():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from app.services.runtime.remote_update import UpdateSession

    process = SimpleNamespace(
        stdout=SimpleNamespace(readline=AsyncMock(side_effect=['{"event":"locked"}\n', ""])),
        stdin=Mock(),
        close=Mock(),
        wait_closed=AsyncMock(),
    )
    process.stdin.write_eof.side_effect = OSError("SSH disconnected")
    conn = SimpleNamespace(create_process=AsyncMock(return_value=process))
    with pytest.raises(RemoteMacError, match="connection lost"):
        async with UpdateSession(conn, "/new/helper", "/root") as session:
            await session.request("activate", manifest=NEW)
    process.stdin.write.assert_called_once()
    conn.create_process.assert_awaited_once()
    process.close.assert_called_once()
