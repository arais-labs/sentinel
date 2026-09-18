import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers import machines
from app.services.runtime import remote_verify as verify
from app.services.runtime.remote_mac import RuntimeUnavailable
from tests.test_remote_mac import machine


@pytest.fixture
def installation(monkeypatch):
    root = "/Users/test/.sentinel/runtime"
    hashes = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(13)]
    manifest = {
        "executable": root + "/releases/test/sentinel-workspace-runtime",
        "kernel": root + "/releases/test/kernel",
        "initImage": "init",
        "workspaceImage": "workspace",
        "version": hashlib.sha256(json.dumps([*hashes, "init", "workspace"]).encode()).hexdigest()[
            :16
        ],
    }
    documents = {"manifest.json": manifest, "update.json": {"phase": "complete"}}

    @asynccontextmanager
    async def open_file(path):
        yield SimpleNamespace(
            read=AsyncMock(return_value=json.dumps(documents[path.rsplit("/", 1)[1]]))
        )

    @asynccontextmanager
    async def sftp():
        yield SimpleNamespace(open=open_file)

    conn = SimpleNamespace(
        start_sftp_client=sftp,
        run=AsyncMock(
            side_effect=[
                SimpleNamespace(
                    stdout="\n".join(f"{digest}  file" for digest in hashes),
                    exit_status=0,
                ),
                SimpleNamespace(exit_status=0),
                SimpleNamespace(exit_status=0),
            ]
        ),
    )
    client = SimpleNamespace(_ensure_conn=AsyncMock(return_value=conn), close=AsyncMock())
    monkeypatch.setattr(verify, "SSHClient", lambda credentials: client)
    status = AsyncMock(return_value={"states": {"workspace": "running"}})
    monkeypatch.setattr(verify, "service_request", status)
    return SimpleNamespace(
        machine=machine(host_key="pinned-key", runtime_root=root),
        documents=documents,
        conn=conn,
        client=client,
        status=status,
    )


@pytest.mark.asyncio
async def test_verification_checks_all_assets_and_only_requests_status(installation):
    report = await verify.verify_installation(installation.machine)
    assert all(check["status"] == "passed" for check in report["checks"])
    commands = [call.args[0] for call in installation.conn.run.call_args_list]
    assert "graphics/guest-bridge.py" in commands[0]
    assert "graphics/libEGL.dylib" in commands[0]
    assert "codesign --verify --strict" in commands[1]
    assert "codesign --verify --strict" in commands[2]
    installation.status.assert_awaited_once_with(
        installation.conn,
        installation.documents["manifest.json"],
        installation.machine.runtime_root,
        "status",
    )
    installation.client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_output,exit_status",
    [("", 1), ("0" * 64 + "  file\n", 0), ("\n".join(["0" * 64 + "  file"] * 8), 0)],
)
async def test_missing_or_changed_files_never_execute_installed_helper(
    installation, bad_output, exit_status
):
    installation.conn.run.side_effect = [
        SimpleNamespace(stdout=bad_output, exit_status=exit_status)
    ]
    report = await verify.verify_installation(installation.machine)
    assert report["checks"][-1]["status"] == "failed"
    installation.status.assert_not_called()
    assert installation.conn.run.await_count == 1


@pytest.mark.asyncio
async def test_invalid_signature_does_not_probe_service(installation):
    results = list(installation.conn.run.side_effect)
    results[1] = SimpleNamespace(exit_status=1)
    installation.conn.run.side_effect = results
    report = await verify.verify_installation(installation.machine)
    assert any(check["status"] == "failed" for check in report["checks"])
    installation.status.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "executable",
    [
        "/tmp/other",
        "/Users/test/.sentinel/runtime/releases/../../sentinel-workspace-runtime",
    ],
)
async def test_invalid_manifest_paths_are_not_executed(installation, executable):
    installation.documents["manifest.json"]["executable"] = executable
    report = await verify.verify_installation(installation.machine)
    assert report["checks"][0]["status"] == "failed"
    installation.conn.run.assert_not_called()
    installation.status.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,expected",
    [(RuntimeUnavailable("stopped"), "warning"), (TimeoutError(), "failed")],
)
async def test_service_probe_failure_is_reported_without_repair(installation, failure, expected):
    installation.status.side_effect = failure
    report = await verify.verify_installation(installation.machine)
    assert report["checks"][-1]["status"] == expected
    assert installation.status.await_count == 1
    installation.client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_verification_during_incomplete_update_is_inconclusive(installation):
    installation.documents["update.json"] = {"phase": "activating"}
    report = await verify.verify_installation(installation.machine)
    assert report["checks"][0]["status"] == "warning"
    installation.conn.run.assert_not_called()
    installation.status.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_remote_update_invalidates_verification(installation):
    async def changed(*args):
        installation.documents["manifest.json"]["version"] = "changed"
        return {"states": {}}

    installation.status.side_effect = changed
    report = await verify.verify_installation(installation.machine)
    assert report["checks"] == [
        {
            "name": "Installation changed",
            "status": "warning",
            "detail": "The installation changed during verification. Run verification again after the update finishes.",
        }
    ]


@pytest.mark.asyncio
async def test_verify_endpoint_requires_enrolled_identity_before_connection(
    monkeypatch,
):
    from app.services.runtime import machines as service

    monkeypatch.setattr(machines.machines_module, "get_machine", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        service, "resolve_machine_secret", lambda row: machine(runtime_root="/root")
    )
    check = AsyncMock()
    monkeypatch.setattr(verify, "verify_installation", check)
    with pytest.raises(HTTPException) as error:
        await machines.verify_remote_runtime(uuid4(), AsyncMock())
    assert error.value.status_code == 409
    check.assert_not_called()


@pytest.mark.asyncio
async def test_verify_endpoint_returns_results_without_installation(monkeypatch, installation):
    from app.services.runtime import machines as service

    monkeypatch.setattr(machines.machines_module, "get_machine", AsyncMock(return_value=object()))
    monkeypatch.setattr(service, "resolve_machine_secret", lambda row: installation.machine)
    check = AsyncMock(return_value={"checks": []})
    monkeypatch.setattr(verify, "verify_installation", check)
    assert await machines.verify_remote_runtime(installation.machine.id, AsyncMock()) == {
        "checks": []
    }
    check.assert_awaited_once_with(installation.machine)


@pytest.mark.asyncio
async def test_reinstall_endpoint_preserves_running_workspace_approval(monkeypatch, installation):
    from app.services.runtime import (
        machines as service,
    )
    from app.services.runtime import (
        remote_mac,
        workspace_containers,
    )
    from app.services.runtime.remote_mac import UpdateApprovalRequired

    monkeypatch.setattr(machines.machines_module, "get_machine", AsyncMock(return_value=object()))
    monkeypatch.setattr(service, "resolve_machine_secret", lambda row: installation.machine)
    assets = {"executable": "shipped-helper"}
    monkeypatch.setattr(workspace_containers, "local_request", AsyncMock(return_value=assets))
    monkeypatch.setattr(remote_mac, "plan_installation", AsyncMock(return_value=[]))
    details = [{"id": "workspace", "name": "Started during upload"}]
    monkeypatch.setattr(service, "runtime_workspace_labels", AsyncMock(return_value=details))
    install = AsyncMock(side_effect=UpdateApprovalRequired(["workspace"]))
    monkeypatch.setattr(remote_mac, "install", install)
    result = await machines.install_remote_runtime(
        installation.machine.id,
        machines.RuntimeInstallRequest(approved=True, reinstall=True, host_key="pinned-key"),
        AsyncMock(),
    )
    assert result == {
        "approval_required": True,
        "workspaces": ["workspace"],
        "workspace_details": details,
    }
    install.assert_awaited_once_with(installation.machine, "pinned-key", assets, [], reinstall=True)
