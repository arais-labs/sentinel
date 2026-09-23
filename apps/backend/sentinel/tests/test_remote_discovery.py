import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncssh
import pytest

from app.routers import machines
from app.services.runtime import remote_mac
from tests.test_remote_mac import machine


@pytest.mark.asyncio
@pytest.mark.parametrize("installed", [True, False])
async def test_new_machine_reads_existing_installation(monkeypatch, installed):
    paths = []

    @asynccontextmanager
    async def open_file(path):
        paths.append(path)
        if not installed or path.endswith("update.json"):
            raise asyncssh.SFTPNoSuchFile("Missing")
        yield SimpleNamespace(read=AsyncMock(return_value=json.dumps({"version": "existing"})))

    @asynccontextmanager
    async def sftp():
        yield SimpleNamespace(open=open_file)

    conn = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(stdout="/Users/test\n")),
        start_sftp_client=sftp,
    )
    client = SimpleNamespace(_ensure_conn=AsyncMock(return_value=conn), close=AsyncMock())
    monkeypatch.setattr(remote_mac, "SSHClient", lambda credentials: client)
    result = await remote_mac.inspect_installation(machine(host_key="observed-key"))
    assert result["installed"] is installed
    assert result["installed_version"] == ("existing" if installed else None)
    assert result["path"] == "/Users/test/.sentinel/runtime"
    assert paths == [
        "/Users/test/.sentinel/runtime/manifest.json",
        "/Users/test/.sentinel/runtime/update.json",
        "/Users/test/.sentinel/runtime/workspaces.json",
    ]
    client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_key", [None, "same-key", "changed-key"])
async def test_inspection_automatically_checks_new_hosts_but_never_changed_identity(
    monkeypatch, saved_key
):
    resolved = machine(host_key=saved_key)
    monkeypatch.setattr(machines.machines_module, "get_machine", AsyncMock(return_value=resolved))
    monkeypatch.setattr(machines.machines_module, "resolve_machine_secret", lambda row: row)
    monkeypatch.setattr(
        remote_mac,
        "fingerprint",
        AsyncMock(return_value={"host_key": "same-key", "fingerprint": "fingerprint"}),
    )
    inspect = AsyncMock(
        return_value={
            "installed": True,
            "installed_version": "existing",
            "path": "/Users/test/.sentinel/runtime",
        }
    )
    monkeypatch.setattr(remote_mac, "inspect_installation", inspect)
    monkeypatch.setattr(remote_mac, "available_version", AsyncMock(return_value="existing"))
    db = AsyncMock()
    result = await machines.inspect_remote_runtime(resolved.id, db)
    if saved_key == "changed-key":
        inspect.assert_not_awaited()
        assert result["host_key_changed"]
    else:
        inspect.assert_awaited_once()
        assert inspect.call_args.args[0].host_key == "same-key"
        assert result["installed"]
        assert result["installed_version"] == "existing"
        assert result["path"] == "/Users/test/.sentinel/runtime"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError(), PermissionError("Access denied")])
async def test_failed_discovery_does_not_report_not_installed(monkeypatch, failure):
    resolved = machine()
    monkeypatch.setattr(machines.machines_module, "get_machine", AsyncMock(return_value=resolved))
    monkeypatch.setattr(machines.machines_module, "resolve_machine_secret", lambda row: row)
    monkeypatch.setattr(remote_mac, "fingerprint", AsyncMock(return_value={"host_key": "key"}))
    monkeypatch.setattr(remote_mac, "inspect_installation", AsyncMock(side_effect=failure))
    with pytest.raises(machines.HTTPException) as error:
        await machines.inspect_remote_runtime(resolved.id, AsyncMock())
    assert error.value.status_code == 502
