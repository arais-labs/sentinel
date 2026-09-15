from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.services.runtime import uploads


@pytest.fixture
def upload_workspace(tmp_path, monkeypatch):
    workspace = SimpleNamespace(
        id=uuid4(),
        directory=str(tmp_path),
        development_tools=[],
        machine_id=uuid4(),
        distribution="ubuntu",
    )
    processes = []

    class Transport:
        def __init__(self, workspace_id, directory, tools):
            assert workspace_id == workspace.id
            self.process = None

        async def create_process(self, command, *, start_if_needed):
            assert start_if_needed is False
            p = await asyncio.create_subprocess_exec(
                "bash",
                "-c",
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            processes.append(p)
            finished = asyncio.create_task(p.wait())

            async def send(data):
                if data is None:
                    p.stdin.close()
                else:
                    assert len(data) <= uploads.BLOCK_SIZE
                    p.stdin.write(data)
                    await p.stdin.drain()

            self.process = SimpleNamespace(
                stdout=p.stdout,
                send_input=send,
                finished=finished,
                wait=lambda: asyncio.shield(finished),
            )
            return self.process

        async def close(self):
            if self.process is not None:
                await self.process.finished

    monkeypatch.setattr(uploads, "ContainerTransport", Transport)
    return workspace


async def transfer(workspace, data=b"", *, name="file.bin", destination="", kind="file", size=None):
    async def chunks():
        # Exercise arbitrary HTTP chunk boundaries, including empty chunks.
        yield b""
        for offset in range(0, len(data), 79037):
            yield data[offset : offset + 79037]

    return await uploads.upload_file(
        workspace,
        destination=destination,
        name=name,
        size=len(data) if size is None else size,
        kind=kind,
        chunks=chunks(),
    )


@pytest.mark.asyncio
async def test_stream_binary_nested_folder_and_collision(upload_workspace):
    workspace = upload_workspace
    root = Path(workspace.directory)
    data = bytes(range(256)) * 9000
    result = await transfer(workspace, data, name='folder/résumé "$(no).bin')
    assert (root / result["path"]).read_bytes() == data
    again = await transfer(workspace, b"new", name='folder/résumé "$(no).bin')
    assert again["name"] == 'résumé "$(no) (1).bin'
    assert (root / result["path"]).read_bytes() == data
    assert (root / again["path"]).read_bytes() == b"new"
    assert not list(root.rglob(".sentinel-upload-*"))


@pytest.mark.asyncio
async def test_empty_file_directory_and_absolute_destination(upload_workspace):
    workspace = upload_workspace
    root = Path(workspace.directory)
    await transfer(workspace, name="empty-folder", kind="directory")
    assert (root / "empty-folder").is_dir()
    result = await transfer(workspace, destination=str(root / "empty-folder"))
    assert (root / result["path"]).read_bytes() == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [1, 500000])
async def test_interrupted_or_oversized_upload_does_not_publish(upload_workspace, size):
    with pytest.raises(ValueError):
        await transfer(upload_workspace, b"partial", size=size)
    assert list(Path(upload_workspace.directory).iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    ["../outside", "/absolute", "dir/../escape", "dir//name", "dir\\escape", "nul\0x"],
)
async def test_rejects_unsafe_imported_names(upload_workspace, name):
    with pytest.raises(ValueError):
        await transfer(upload_workspace, b"data", name=name)
    assert list(Path(upload_workspace.directory).iterdir()) == []


@pytest.mark.asyncio
async def test_symlink_does_not_overwrite_or_redirect(upload_workspace, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="conflicts"):
        await transfer(upload_workspace, b"no", name="link/file")
    (tmp_path / "existing").symlink_to(outside / "missing")
    result = await transfer(upload_workspace, b"safe", name="existing")
    assert result["name"] == "existing (1)"
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_cancel_cleans_partial_file(upload_workspace):
    started = asyncio.Event()

    async def chunks():
        yield b"x" * uploads.BLOCK_SIZE
        started.set()
        await asyncio.Future()

    task = asyncio.create_task(
        uploads.upload_file(
            upload_workspace,
            destination="",
            name="partial",
            size=1000000,
            kind="file",
            chunks=chunks(),
        )
    )
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(Path(upload_workspace.directory).iterdir()) == []


@pytest.mark.asyncio
async def test_upload_route_targets_selected_workspace_and_rejects_missing(
    upload_workspace,
):
    from fastapi import FastAPI
    from app.dependencies import get_db
    from app.routers.workspace_browser import router
    from app.models import Workspace

    app = FastAPI()
    app.include_router(router)
    workspace = upload_workspace
    db = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, key: workspace if key == workspace.id else None),
        close=AsyncMock(),
    )
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/workspaces/{workspace.id}/browse/upload",
            params={"name": "from-desktop.bin", "size": 4},
            content=b"\0abc",
        )
        assert response.status_code == 200, response.text
        assert Path(workspace.directory, "from-desktop.bin").read_bytes() == b"\0abc"
        db.get.assert_awaited_with(Workspace, workspace.id)
        assert (
            await client.post(
                f"/workspaces/{uuid4()}/browse/upload", params={"name": "x", "size": 0}
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/workspaces/{workspace.id}/browse/upload",
                params={"name": "../bad", "size": 0},
            )
        ).status_code == 422


@pytest.mark.asyncio
async def test_concurrent_uploads_do_not_replace_each_other(upload_workspace):
    results = await asyncio.gather(
        transfer(upload_workspace, b"first", name="same.txt"),
        transfer(upload_workspace, b"second", name="same.txt"),
    )
    assert {result["name"] for result in results} == {"same.txt", "same (1).txt"}
    assert {
        Path(upload_workspace.directory, result["path"]).read_bytes() for result in results
    } == {b"first", b"second"}


@pytest.mark.asyncio
async def test_progress_tracks_guest_acknowledgements_and_instance_scope(
    upload_workspace,
):
    acknowledgements = []
    data = b"x" * (uploads.BLOCK_SIZE * 2 + 7)

    async def chunks():
        yield data

    await uploads.upload_file(
        upload_workspace,
        destination="",
        name="progress.bin",
        size=len(data),
        kind="file",
        chunks=chunks(),
        progress=acknowledgements.append,
    )
    assert acknowledgements == [uploads.BLOCK_SIZE, uploads.BLOCK_SIZE * 2, len(data)]
    transfer_id = str(uuid4())
    key = ("first-instance", str(upload_workspace.id), transfer_id)
    uploads.record_progress(key, 256)
    assert uploads.get_progress(key) == 256
    assert uploads.get_progress(("second-instance", str(upload_workspace.id), transfer_id)) == 0


@pytest.mark.asyncio
async def test_upload_transport_does_not_start_stopped_workspace(monkeypatch):
    from app.services.runtime.container_transport import ContainerTransport
    from app.services.runtime.workspace_containers import WorkspaceContainerError

    transport = ContainerTransport(uuid4(), "/project", [])
    monkeypatch.setattr(transport, "is_ready", AsyncMock(return_value=False))
    start = AsyncMock()
    monkeypatch.setattr(transport, "wait_ready", start)
    with pytest.raises(WorkspaceContainerError, match="Start this workspace"):
        await transport.create_process("unused", start_if_needed=False)
    start.assert_not_awaited()
