import asyncio
import os
import io
import zipfile
import shutil

from fastapi import HTTPException
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.runtime import file_stream
from app.middleware.security_headers import SecurityHeadersMiddleware
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media_type,download,framed",
    [
        ("application/pdf", False, True),
        ("image/png", False, True),
        ("image/svg+xml", False, True),
        ("audio/wav", False, True),
        ("video/webm", False, True),
        ("application/pdf", True, False),
        ("text/plain", False, False),
        ("text/html", False, False),
        ("application/octet-stream", False, False),
    ],
)
async def test_preview_framing_through_security_middleware(media_type, download, framed):
    async def content():
        yield b"test content"

    async def close():
        pass

    transfer = SimpleNamespace(
        metadata={"media_type": media_type, "length": 12, "name": "file", "status": 200},
        content=content,
        close=close,
    )

    async def app(scope, receive, send):
        response = await file_stream.stream_response(transfer, download=download)
        await response(scope, receive, send)

    async with AsyncClient(
        transport=ASGITransport(app=SecurityHeadersMiddleware(app)), base_url="http://test"
    ) as client:
        response = await client.get("/")
    assert response.content == b"test content"
    if framed:
        assert "x-frame-options" not in response.headers
        assert (
            "frame-ancestors 'self' sentinel://app http://localhost:* http://127.0.0.1:*"
            in response.headers["content-security-policy"]
        )
    else:
        assert response.headers["x-frame-options"] == "DENY"
    if media_type in {"image/svg+xml", "text/html"}:
        assert "sandbox; default-src 'none'" in response.headers["content-security-policy"]


@pytest.fixture
def file_workspace(tmp_path, monkeypatch):
    workspace = SimpleNamespace(id=uuid4(), directory=str(tmp_path), development_tools=[])
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
            self.process = p

            async def send(data):
                p.stdin.write(data)
                await p.stdin.drain()

            return SimpleNamespace(stdout=p.stdout, reader=p.stdout, send_input=send)

        async def close(self):
            if self.process:
                self.process.stdin.close()
                await self.process.wait()

    monkeypatch.setattr(file_stream, "ContainerTransport", Transport)
    return workspace, processes


@pytest.mark.asyncio
async def test_stream_exceeds_exec_capture_limit(file_workspace):
    workspace, processes = file_workspace
    contents = bytes(range(256)) * 21000
    Path(workspace.directory, "report with spaces.pdf").write_bytes(contents)
    transfer = await file_stream.open_file(workspace, "report with spaces.pdf")
    assert transfer.metadata["size"] == len(contents)
    assert b"".join([chunk async for chunk in transfer.content()]) == contents
    assert all(p.returncode == 0 for p in processes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header,expected",
    [
        ("bytes=0-9", b"0123456789"),
        ("bytes=7-", b"789"),
        ("bytes=-3", b"789"),
        ("bytes=8-99", b"89"),
    ],
)
async def test_byte_ranges(file_workspace, header, expected):
    workspace, _ = file_workspace
    Path(workspace.directory, "video.mp4").write_bytes(b"0123456789")
    transfer = await file_stream.open_file(workspace, "video.mp4", range_header=header)
    assert transfer.metadata["status"] == 206
    assert b"".join([chunk async for chunk in transfer.content()]) == expected


@pytest.mark.asyncio
async def test_closing_preview_closes_guest_reader(file_workspace):
    workspace, processes = file_workspace
    Path(workspace.directory, "large.pdf").write_bytes(b"x" * 1000000)
    transfer = await file_stream.open_file(workspace, "large.pdf")
    stream = transfer.content()
    assert len(await anext(stream)) == 256 * 1024
    await stream.aclose()
    assert all(p.returncode is not None for p in processes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header", ["bytes=99-", "bytes=8-2", "bytes=-0", "bytes=0-1,4-5", "nonsense"]
)
async def test_invalid_ranges_close_reader(file_workspace, header):
    workspace, processes = file_workspace
    Path(workspace.directory, "file").write_bytes(b"0123456789")
    with pytest.raises(HTTPException) as error:
        await file_stream.open_file(workspace, "file", range_header=header)
    assert error.value.status_code == 416
    assert error.value.headers["Content-Range"] == "bytes */10"
    assert all(p.returncode is not None for p in processes)


@pytest.mark.asyncio
async def test_head_and_if_range(file_workspace):
    workspace, processes = file_workspace
    Path(workspace.directory, "vidéo.mp4").write_bytes(b"0123456789")
    response = await file_stream.file_response(workspace, "vidéo.mp4", head=True)
    assert response.body == b""
    assert response.headers["content-length"] == "10"
    assert response.headers["content-type"] == "video/mp4"
    assert "vid%C3%A9o.mp4" in response.headers["content-disposition"]
    assert all(p.returncode is not None for p in processes)
    transfer = await file_stream.open_file(
        workspace, "vidéo.mp4", range_header="bytes=5-", if_range=response.headers["etag"]
    )
    assert b"".join([chunk async for chunk in transfer.content()]) == b"56789"
    transfer = await file_stream.open_file(
        workspace, "vidéo.mp4", range_header="bytes=5-", if_range='"changed"'
    )
    assert transfer.metadata["status"] == 200
    assert b"".join([chunk async for chunk in transfer.content()]) == b"0123456789"


@pytest.mark.asyncio
async def test_invalid_paths_and_non_regular_files(file_workspace):
    workspace, processes = file_workspace
    os.mkfifo(Path(workspace.directory, "pipe"))
    for path, status in [("missing", 404), ("bad\0name", 422), ("pipe", 422), (".", 422)]:
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(file_stream.open_file(workspace, path), 3)
        assert error.value.status_code == status
    assert all(p.returncode is not None for p in processes)


@pytest.mark.asyncio
async def test_directory_download_outside_project_and_empty_directory(file_workspace, tmp_path):
    workspace, _ = file_workspace
    folder = tmp_path.parent / (tmp_path.name + "-outside")
    folder.mkdir()
    (folder / "empty").mkdir()
    (folder / "config.txt").write_bytes(b"settings")
    (folder / "broken").symlink_to(folder / "missing")
    try:
        transfer = await file_stream.open_file(workspace, str(folder), download=True)
        archive = b"".join([chunk async for chunk in transfer.content()])
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            assert zipped.read("config.txt") == b"settings"
            assert "empty/" in zipped.namelist()
            assert "broken" not in zipped.namelist()
    finally:
        shutil.rmtree(folder)


@pytest.mark.asyncio
async def test_reader_waits_for_acknowledgement(file_workspace):
    workspace, processes = file_workspace
    Path(workspace.directory, "large").write_bytes(b"x" * 1000000)
    transfer = await file_stream.open_file(workspace, "large")
    assert (
        len(await transfer.process.reader.readexactly(file_stream.BLOCK_SIZE))
        == file_stream.BLOCK_SIZE
    )
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(transfer.process.reader.read(1), 0.05)
    await transfer.close()
    assert all(p.returncode is not None for p in processes)


@pytest.mark.asyncio
async def test_file_truncated_during_transfer_never_appends_error_text(file_workspace):
    workspace, processes = file_workspace
    path = Path(workspace.directory, "changing.bin")
    path.write_bytes(b"x" * 1000000)
    transfer = await file_stream.open_file(workspace, "changing.bin")
    stream = transfer.content()
    assert len(await anext(stream)) == file_stream.BLOCK_SIZE
    path.write_bytes(b"")
    with pytest.raises(asyncio.IncompleteReadError) as error:
        await anext(stream)
    assert error.value.partial == b""
    assert all(p.returncode == 1 for p in processes)
