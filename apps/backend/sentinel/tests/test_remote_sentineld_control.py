"""The backend's update-time control of a remote sentineld, over its socket (DESIGN.md §4)."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile

import pytest

from app.services.workspaces.machines.remote import worker as remote
from app.services.workspaces.machines.remote import update as remote_update

SENTINELD = Path(__file__).resolve().parents[4] / "packages/sentineld/build/bin/sentineld"


class Conn:
    """The two asyncssh calls the control path uses, served locally."""

    async def open_unix_connection(self, path, encoding=None):
        reader, writer = await asyncio.open_unix_connection(path)

        class Reader:
            async def readline(self):
                return (await reader.readline()).decode()

            async def read(self, size):
                return (await reader.read(size)).decode()

        class Writer:
            def write(self, text):
                writer.write(text.encode())

            def close(self):
                writer.close()

        return Reader(), Writer()

    async def run(self, command, **kwargs):
        process = await asyncio.create_subprocess_shell(command, stdout=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        return type("Result", (), {"stdout": stdout.decode(), "exit_status": process.returncode})

    async def create_process(self, command, encoding=None):
        process = await asyncio.create_subprocess_shell(
            command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
        )

        class Stdin:
            def write(self, text):
                process.stdin.write(text.encode())

            def write_eof(self):
                process.stdin.close()

        class Stdout:
            async def readline(self):
                return (await process.stdout.readline()).decode()

        class Process:
            stdin, stdout = Stdin(), Stdout()

            async def wait_closed(self):
                await process.wait()

            def close(self):
                if process.returncode is None:
                    process.kill()

        return Process()


@pytest.fixture
def root():
    # A short path: Unix socket addresses are limited to 104 bytes.
    path = tempfile.mkdtemp(prefix="sentineld-", dir="/tmp")
    os.makedirs(path + "/state")
    yield path
    shutil.rmtree(path, ignore_errors=True)


async def fake_sentineld(root, workspaces):
    requests = []

    async def serve(reader, writer):
        while line := await reader.readline():
            request = json.loads(line)
            requests.append(request)
            reply = {"id": request["id"]}
            if request["op"] == "list":
                reply["workspaces"] = workspaces
            elif request["op"] == "stop":
                workspaces[request["workspace"]]["state"] = "stopped"
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
            if request["op"] == "shutdown":
                writer.close()
                server.close()
                return

    server = await asyncio.start_unix_server(serve, root + "/state/sentineld.sock")
    return server, requests


@pytest.mark.asyncio
async def test_status_lists_workspace_states(root):
    server, requests = await fake_sentineld(root, {"w1": {"state": "running", "operation": None}})
    try:
        assert await remote.service_request(Conn(), {}, root, "status") == {
            "states": {"w1": "running"}
        }
        assert [r["op"] for r in requests] == ["status", "list"]
        assert requests[0]["api"] == 1
    finally:
        server.close()


@pytest.mark.asyncio
async def test_maintenance_refuses_unapproved_running_workspaces(root):
    workspaces = {"w1": {"state": "running", "operation": None}}
    server, requests = await fake_sentineld(root, workspaces)
    try:
        with pytest.raises(remote.RemoteMacError, match="approve the update again"):
            await remote.service_request(Conn(), {}, root, "maintenance", approved_workspaces=[])
        assert [r["op"] for r in requests] == ["status", "list"]
    finally:
        server.close()


@pytest.mark.asyncio
async def test_maintenance_stops_approved_workspaces_then_shuts_down(root):
    workspaces = {
        "w1": {"state": "running", "operation": None},
        "w2": {"state": "stopped", "operation": "preparing"},
        "w3": {"state": "stopped", "operation": None},
    }
    server, requests = await fake_sentineld(root, workspaces)
    result = await remote.service_request(
        Conn(), {}, root, "maintenance", approved_workspaces=["w1", "w2"]
    )
    assert [(r["op"], r.get("workspace")) for r in requests[2:]] == [
        ("stop", "w1"),
        ("stop", "w2"),
        ("shutdown", None),
    ]
    assert result == {"states": {"w1": "stopped", "w2": "stopped", "w3": "stopped"}}


@pytest.mark.asyncio
async def test_no_listener_is_unavailable_and_readiness_waits_for_one(root):
    with pytest.raises(remote.RuntimeUnavailable):
        await remote.service_request(Conn(), {}, root, "status")
    started = []

    async def start_later():
        await asyncio.sleep(0.3)
        started.append(await fake_sentineld(root, {}))

    task = asyncio.create_task(start_later())
    await remote.wait_for_runtime_ready(Conn(), root, timeout=5, retry_interval=0.05)
    await task
    started[0][0].close()


@pytest.mark.asyncio
async def test_readiness_timeout_reports_the_service_log(root):
    Path(root, "service.log").write_text("sentineld: something broke\n")
    with pytest.raises(remote.RemoteMacError, match="something broke"):
        await remote.wait_for_runtime_ready(Conn(), root, timeout=0.3, retry_interval=0.05)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not SENTINELD.exists(), reason="sentineld is not built (make -C packages/sentineld)"
)
async def test_real_sentineld_update_handoff(root):
    """The coordinator activates a release, hands it the store, and maintenance frees it."""
    release = root + "/releases/r1"
    os.makedirs(release)
    os.symlink(SENTINELD.parents[1], release + "/sentineld")
    manifest = {
        "executable": release + "/sentineld/bin/sentineld",
        # As the backend writes it for releases before the merge: the coordinator ignores it.
        "workspace": release + "/sentinel-workspace",
        "version": "0123456789abcdef",
        "updateProtocol": 1,
        "storeVersion": 1,
    }
    conn = Conn()
    async with remote_update.UpdateSession(conn, manifest["executable"], root) as session:
        assert (await session.request("inspect"))["owner_free"]
        await session.request("activate", manifest=manifest)
        await remote.wait_for_runtime_ready(conn, root, timeout=10)
        assert not (await session.request("inspect"))["owner_free"]
        assert await remote.service_request(conn, manifest, root, "status") == {"states": {}}
        await remote.service_request(conn, manifest, root, "maintenance", approved_workspaces=[])
        for _ in range(100):
            if (await session.request("inspect"))["owner_free"]:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("sentineld kept the store after shutdown")
    assert os.readlink(root + "/current") == release
    assert json.loads(Path(root, "manifest.json").read_text()) == manifest
