"""One bounded guest byte stream for downloads, PDFs, images, audio and video."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import quote

from fastapi import HTTPException
from starlette.responses import Response, StreamingResponse

from app.services.runtime.container_transport import ContainerTransport
from app.services.runtime.guest_commands import guest_python_command

BLOCK_SIZE = 256 * 1024


class FileStream:
    def __init__(self, transport, process, metadata):
        self.transport = transport
        self.process = process
        self.metadata = metadata
        self.closed = False

    async def close(self):
        if not self.closed:
            self.closed = True
            await self.transport.close()

    async def content(self):
        try:
            remaining = self.metadata["length"]
            while remaining:
                # Timeout only guest progress, not time spent paused by a media player.
                async with asyncio.timeout(65):
                    block = await self.process.reader.readexactly(min(BLOCK_SIZE, remaining))
                remaining -= len(block)
                yield block
                await self.process.send_input(b"next\n")
        finally:
            await self.close()


async def open_file(
    workspace, path, *, range_header=None, if_range=None, head=False, download=False
):
    transport = ContainerTransport(workspace.id, workspace.directory, workspace.development_tools)
    try:
        command = guest_python_command(
            "common/files/read.py",
            [
                json.dumps(
                    {
                        "workspace": workspace.directory,
                        "path": path,
                        "range": range_header,
                        "if_range": if_range,
                        "head": head,
                        "download": download,
                    }
                )
            ],
        )
        async with asyncio.timeout(125):
            process = await transport.create_process(command, start_if_needed=False)
            line = await process.stdout.readline()
            try:
                metadata = json.loads(line)
            except (ValueError, TypeError) as error:
                raise OSError("The workspace closed the file transfer unexpectedly") from error
        if not metadata.get("ok"):
            headers = (
                {"Content-Range": f'bytes */{metadata["size"]}'}
                if metadata.get("status") == 416
                else None
            )
            raise HTTPException(
                metadata.get("status", 422),
                metadata.get("detail", "Could not read file"),
                headers=headers,
            )
        return FileStream(transport, process, metadata)
    except BaseException:
        await transport.close()
        raise


class _FileResponse(StreamingResponse):
    def __init__(self, transfer, **kwargs):
        self.transfer = transfer
        super().__init__(transfer.content(), **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.transfer.close()


async def file_response(
    workspace, path, *, range_header=None, if_range=None, head=False, download=False
):
    transfer = await open_file(
        workspace, path, range_header=range_header, if_range=if_range, head=head, download=download
    )
    return await stream_response(transfer, head=head, download=download)


async def stream_response(transfer, *, head=False, download=False):
    data = transfer.metadata
    media_type = data["media_type"]
    inline = media_type == "application/pdf" or media_type.startswith(
        ("image/", "audio/", "video/")
    )
    disposition = "attachment" if download or not inline else "inline"
    headers = {
        "Content-Length": str(data["length"]),
        "Content-Disposition": f"{disposition}; filename*=UTF-8''" + quote(data["name"], safe=""),
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if data.get("etag"):
        headers["ETag"] = data["etag"]
    if data["status"] == 206:
        headers["Content-Range"] = (
            f'bytes {data["start"]}-{data["start"] + data["length"] - 1}/{data["size"]}'
        )
    if media_type in {"text/html", "application/xhtml+xml", "image/svg+xml"}:
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    if disposition == "inline":
        # Preview responses may be embedded by the desktop and local dev renderer.
        policy = "frame-ancestors 'self' sentinel://app http://localhost:* http://127.0.0.1:*"
        existing = headers.get("Content-Security-Policy")
        headers["Content-Security-Policy"] = f"{existing}; {policy}" if existing else policy
    if head:
        await transfer.close()
        return Response(status_code=data["status"], media_type=media_type, headers=headers)
    return _FileResponse(
        transfer, status_code=data["status"], media_type=media_type, headers=headers
    )
