from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers.machines import RuntimeInstallRequest, install_remote_runtime
from app.services.runtime.machines import ResolvedMachine
from app.services.runtime.remote_mac import RemoteMacError, RemoteMacRuntime, install
from app.services.runtime.ssh_client import SSHClient


def machine(**updates):
    return replace(
        ResolvedMachine(
            id=uuid4(),
            name="remote",
            provider="ssh",
            host="mac.example",
            port=22,
            username="user",
            auth_type="password",
            secret="test",
            updated_at_marker="1",
        ),
        **updates,
    )


@pytest.mark.asyncio
async def test_install_requires_explicit_approval_before_accessing_machine():
    db = AsyncMock()
    with pytest.raises(HTTPException) as caught:
        await install_remote_runtime(
            uuid4(), RuntimeInstallRequest(approved=False, host_key="key"), db
        )
    assert caught.value.status_code == 422
    db.get.assert_not_called()


def test_remote_runtime_rejects_unenrolled_machine():
    with pytest.raises(RemoteMacError, match="Verify this machine's SSH identity"):
        RemoteMacRuntime(machine())


@pytest.mark.asyncio
async def test_install_pins_approved_key_and_rejects_unsupported_host(monkeypatch):
    import app.services.runtime.remote_mac as remote

    seen = []
    connection = AsyncMock()
    connection.run.return_value.stdout = "Linux x86_64\n26.0\n/home/user\n"

    class Client:
        def __init__(self, credentials):
            seen.append(credentials)

        async def _ensure_conn(self):
            return connection

        async def close(self):
            pass

    monkeypatch.setattr(remote, "SSHClient", Client)
    with pytest.raises(RemoteMacError, match="Apple Silicon"):
        await install(machine(), "approved-key", {})
    assert seen[0].host_key == "approved-key"
    assert connection.run.await_count == 1
    connection.start_sftp_client.assert_not_called()


@pytest.mark.asyncio
async def test_pinned_identity_is_used_for_ssh_connection(monkeypatch):
    import asyncssh

    key = asyncssh.generate_private_key("ssh-ed25519").convert_to_public()
    connect = AsyncMock(return_value=object())
    monkeypatch.setattr(asyncssh, "connect", connect)
    client = SSHClient(machine(host_key=key.export_public_key().decode()).credentials())
    await client._connect()
    trusted = connect.call_args.kwargs["known_hosts"]
    assert trusted[0][0].get_fingerprint() == key.get_fingerprint()


@pytest.mark.asyncio
async def test_closing_remote_connection_does_not_issue_vm_shutdown():
    runtime = RemoteMacRuntime(
        machine(host_key="key", runtime_root="/Users/user/.sentinel/runtime")
    )
    runtime.ssh = AsyncMock()
    runtime.listener = None
    await runtime.close()
    runtime.ssh.close.assert_awaited_once()
    runtime.ssh.run.assert_not_called()


@pytest.mark.asyncio
async def test_remote_mutation_is_not_replayed(monkeypatch):
    import httpx

    runtime = RemoteMacRuntime(
        machine(host_key="key", runtime_root="/Users/user/.sentinel/runtime")
    )
    runtime.connect = AsyncMock()
    runtime.bridge_socket = "/tmp/fake.sock"
    requests = []

    async def fail(request):
        requests.append(request)
        raise httpx.ReadError("connection dropped")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(fail), base_url="http://remote"),
    )
    with pytest.raises(httpx.ReadError):
        await runtime.operation("exec", workspace=str(uuid4()), arguments=["touch", "/tmp/marker"])
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_workspace_operations_route_without_touching_local_runtime(monkeypatch):
    from app.services.runtime import workspace_containers as containers

    remote = AsyncMock()
    remote.operation.return_value = {"exitCode": 0}
    monkeypatch.setattr(containers, "remote_for", AsyncMock(return_value=remote))
    local = AsyncMock()
    monkeypatch.setattr(containers, "local_request", local)
    workspace = str(uuid4())
    assert await containers.request("exec", workspace=workspace, arguments=["true"]) == {
        "exitCode": 0
    }
    remote.operation.assert_awaited_once_with("exec", workspace=workspace, arguments=["true"])
    local.assert_not_called()


@pytest.mark.asyncio
async def test_status_wait_cancellation_does_not_cancel_reconnection(monkeypatch):
    import asyncio

    runtime = RemoteMacRuntime(
        machine(host_key="key", runtime_root="/Users/user/.sentinel/runtime")
    )
    finished = asyncio.Event()

    async def connect():
        await asyncio.sleep(0.05)
        finished.set()

    monkeypatch.setattr(runtime, "_connect", connect)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(runtime.connect(), 0.001)
    await asyncio.wait_for(finished.wait(), 1)
    await runtime.close()


@pytest.mark.asyncio
async def test_changing_address_preserves_pinned_worker_identity(monkeypatch):
    from types import SimpleNamespace

    from app.schemas.machines import MachineUpdateRequest
    from app.services.runtime import machines

    row = SimpleNamespace(
        host="old",
        port=22,
        username="user",
        provider_config={"host_key": "old-key", "runtime_root": "/old"},
    )
    monkeypatch.setattr(machines, "get_machine", AsyncMock(return_value=row))
    await machines.update_machine(AsyncMock(), uuid4(), MachineUpdateRequest(host="new"))
    assert row.provider_config == {"host_key": "old-key", "runtime_root": "/old"}
    await machines.update_machine(AsyncMock(), uuid4(), MachineUpdateRequest(username="different"))
    assert row.provider_config == {}


@pytest.mark.asyncio
async def test_local_workspace_status_does_not_wait_for_remote_hosts(monkeypatch):
    from app.services.runtime import workspace_containers as containers

    workspace = uuid4()
    monkeypatch.setattr(containers, "remote_for", AsyncMock(return_value=None))
    local = AsyncMock(return_value={"states": {str(workspace): {"state": "running"}}})
    overview = AsyncMock(side_effect=AssertionError("Must not poll every machine"))
    monkeypatch.setattr(containers, "local_request", local)
    monkeypatch.setattr(containers, "overview", overview)
    assert (await containers.statuses(workspace))[str(workspace)]["state"] == "running"
    overview.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "running,approved,fail_start,stage_error",
    [
        (False, False, False, None),
        (True, False, False, None),
        (True, True, False, None),
        (True, True, True, None),
        (True, True, False, "upload"),
        (True, True, False, "checksum"),
        (True, True, False, "signature"),
    ],
)
async def test_runtime_upgrade_lifecycle(
    monkeypatch, tmp_path, running, approved, fail_start, stage_error
):
    import hashlib
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import app.services.runtime.remote_mac as remote

    workspace = str(uuid4())
    old = {
        "version": "old",
        "runtimeMigrations": ["001_worker_ownership"],
        "executable": "/old/helper",
        "kernel": "/old/kernel",
        "updateProtocol": 1,
        "storeVersion": 1,
    }
    manifest_path = "/Users/user/.sentinel/runtime/manifest.json"
    files = {manifest_path: json.dumps(old)}
    events = []
    for name in ("helper", "kernel"):
        (tmp_path / name).write_text(name)

    class SFTP:
        @asynccontextmanager
        async def open(self, path, mode="r"):
            class File:
                async def read(self):
                    return files[path]

                async def write(self, value):
                    files[path] = value

            yield File()

        async def put(self, source, dest, **kwargs):
            from pathlib import Path

            if stage_error == "upload":
                raise RemoteMacError("upload interrupted")
            files[dest] = Path(source).read_text()
            events.append("stage")

        async def posix_rename(self, source, dest):
            files[dest] = files.pop(source)
            if dest == manifest_path:
                events.append("activate:" + json.loads(files[dest])["version"])

        async def chmod(self, *args):
            pass

    class Connection:
        @asynccontextmanager
        async def start_sftp_client(self):
            yield SFTP()

        async def run(self, command, **kwargs):
            if "uname" in command:
                return SimpleNamespace(stdout="Darwin arm64\n26.0\n/Users/user\n")
            if "shasum" in command:
                if stage_error == "checksum":
                    return SimpleNamespace(stdout="incorrect checksum")
                return SimpleNamespace(
                    stdout=hashlib.sha256(files[command.split()[-1]].encode()).hexdigest()
                )
            if "codesign" in command and stage_error == "signature":
                raise RemoteMacError("signature verification failed")
            return SimpleNamespace(exit_status=0)

    class Client:
        def __init__(self, *args):
            pass

        async def _ensure_conn(self):
            return Connection()

        async def close(self):
            pass

    class Runtime:
        attempts = 0

        def __init__(self, machine):
            pass

        async def connect(self):
            Runtime.attempts += 1
            events.append("connect")
            if fail_start and Runtime.attempts == 1:
                raise RuntimeError("new runtime failed")

        async def operation(self, action, **values):
            assert action == "workspace_resume"
            assert values["approved_workspaces"] == [workspace]
            events.append("restart")

        async def close(self):
            pass

    async def rpc(conn, manifest, root, action, **values):
        events.append(action)
        if action == "status":
            return {"states": {workspace: "running"} if running else {}}
        assert set(values["approved_workspaces"]).issubset({workspace})
        session.free = True
        return {"states": {workspace: "stopped"} if running else {}}

    class Session:
        free = not running

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, action, **values):
            if action == "migration_status":
                return {"target": ["001_worker_ownership"], "inputs": []}
            if action == "migration_plan":
                events.append("migration_plan")
            if action == "migrate":
                assert self.free
                events.append("migrate")
            if action == "inspect":
                return {
                    "migrations": ["001_worker_ownership"],
                    "manifest": json.loads(files[manifest_path]),
                    "owner_free": self.free,
                    "journal": None,
                }
            if action == "activate":
                assert self.free
                files[manifest_path] = json.dumps(values["manifest"])
                self.free = False
                events.append("activate:" + values["manifest"]["version"])

    session = Session()
    from app.services.runtime import remote_update

    monkeypatch.setattr(remote_update, "UpdateSession", lambda *args: session)
    monkeypatch.setattr(
        remote,
        "wait_for_runtime_ready",
        AsyncMock(
            side_effect=([RemoteMacError("new runtime failed"), None] if fail_start else None)
        ),
    )
    monkeypatch.setattr(remote, "SSHClient", Client)
    monkeypatch.setattr(remote, "RemoteMacRuntime", Runtime)
    monkeypatch.setattr(remote, "service_request", rpc)
    assets = {
        "executable": str(tmp_path / "helper"),
        "kernel": str(tmp_path / "kernel"),
        "initImage": "init@digest",
        "workspaceImage": "workspace@digest",
    }
    (tmp_path / "graphics").mkdir()
    for path, name in remote.runtime_assets(assets)[2:]:
        path.write_text(name)
    args = (machine(), "key", assets, [workspace] if approved else [])
    if stage_error:
        with pytest.raises(RemoteMacError):
            await remote.install(*args)
        assert json.loads(files[manifest_path]) == old
        assert "maintenance" not in events
        assert not any(event.startswith("activate:") for event in events)
    elif running and not approved:
        with pytest.raises(remote.UpdateApprovalRequired) as error:
            await remote.install(*args)
        assert error.value.workspaces == [workspace]
        assert "maintenance" not in events
        assert not any(event.startswith("activate:") for event in events)
        assert json.loads(files[manifest_path]) == old
    elif fail_start:
        with pytest.raises(RemoteMacError, match="previous runtime restored"):
            await remote.install(*args)
        assert json.loads(files[manifest_path]) == old
        assert "restart" not in events
    else:
        result = await remote.install(*args)
        assert result["runtime_version"] != "old"
        if running:
            assert events.index("stage") < events.index("maintenance")
        else:
            assert "maintenance" not in events
        assert ("restart" in events) == running
    assert not remote._maintenance


@pytest.mark.asyncio
async def test_restart_approval_precedes_deployment_preparation(monkeypatch):
    import app.routers.machines as routes
    import app.services.runtime.machines as machines
    import app.services.runtime.remote_mac as remote
    import app.services.runtime.workspace_containers as containers

    enrolled = machine(host_key="key", runtime_root="/Users/user/.sentinel/runtime")
    workspace = str(uuid4())
    details = [{"id": workspace, "name": "Sample Ubuntu", "instance": "test"}]
    monkeypatch.setattr(routes.machines_module, "get_machine", AsyncMock(return_value=object()))
    monkeypatch.setattr(machines, "resolve_machine_secret", lambda row: enrolled)
    monkeypatch.setattr(remote, "plan_installation", AsyncMock(return_value=[workspace]))
    monkeypatch.setattr(machines, "runtime_workspace_labels", AsyncMock(return_value=details))
    prepare, install_mock = AsyncMock(), AsyncMock()
    monkeypatch.setattr(containers, "local_request", prepare)
    monkeypatch.setattr(remote, "install", install_mock)
    result = await routes.install_remote_runtime(
        enrolled.id, RuntimeInstallRequest(approved=True, host_key="key"), AsyncMock()
    )
    assert result == {
        "approval_required": True,
        "workspaces": [workspace],
        "workspace_details": details,
    }
    prepare.assert_not_awaited()
    install_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_install_rechecks_workspaces_before_reading_assets(monkeypatch):
    import app.services.runtime.remote_mac as remote

    connection = AsyncMock()
    connection.run.return_value.stdout = "Darwin arm64\n26.0\n/Users/user\n"
    client = AsyncMock()
    client._ensure_conn.return_value = connection
    monkeypatch.setattr(remote, "SSHClient", lambda credentials: client)
    monkeypatch.setattr(remote, "_running_workspaces", AsyncMock(return_value=["new-workspace"]))
    with pytest.raises(remote.UpdateApprovalRequired) as caught:
        await install(machine(), "key", {})
    assert caught.value.workspaces == ["new-workspace"]
    connection.start_sftp_client.assert_not_called()
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_plan_never_starts_unenrolled_runtime(monkeypatch):
    import app.services.runtime.remote_mac as remote

    client = AsyncMock()
    monkeypatch.setattr(remote, "SSHClient", client)
    assert await remote.plan_installation(machine()) == []
    client.assert_not_called()


@pytest.mark.asyncio
async def test_healthy_connection_does_not_probe_before_every_operation(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import app.services.runtime.remote_mac as remote

    runtime = RemoteMacRuntime(
        machine(host_key="key", runtime_root="/Users/user/.sentinel/runtime")
    )
    runtime.ssh = SimpleNamespace(_conn=SimpleNamespace(is_closed=lambda: False), close=AsyncMock())
    runtime.listener = Mock()
    probe = AsyncMock()
    monkeypatch.setattr(remote, "service_request", probe)
    await runtime.connect()
    await runtime.connect()
    probe.assert_not_awaited()
    runtime.ssh.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_failure_invalidates_connection_without_replaying(monkeypatch):
    from unittest.mock import Mock

    import httpx

    runtime = RemoteMacRuntime(
        machine(host_key="key", runtime_root="/Users/user/.sentinel/runtime")
    )
    runtime.connect = AsyncMock()
    runtime.bridge_socket = "/tmp/fake.sock"
    listener = Mock()
    runtime.listener = listener
    requests = []

    async def fail(request):
        requests.append(request)
        raise httpx.ReadError("response lost")

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(fail), base_url="http://remote"),
    )
    with pytest.raises(httpx.ReadError):
        await runtime.operation("exec", workspace=str(uuid4()), arguments=["true"])
    assert len(requests) == 1
    listener.close.assert_called_once()
    assert runtime.listener is None
