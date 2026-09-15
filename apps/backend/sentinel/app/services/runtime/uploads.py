"""Bounded file uploads over the existing local/remote guest process transport."""

from __future__ import annotations

import asyncio
import json
import shlex
from collections.abc import AsyncIterable
from time import monotonic

from app.services.runtime.container_transport import ContainerTransport
from app.services.runtime.guest_commands import load_guest_command

BLOCK_SIZE = 256 * 1024

# Short-lived observations only; HTTP/process lifetime still owns each transfer.
_progress: dict[tuple[str, str, str], tuple[int, float]] = {}


def record_progress(key: tuple[str, str, str], received: int) -> None:
    now = monotonic()
    for expired in [key for key, (_, touched) in _progress.items() if now - touched > 300]:
        del _progress[expired]
    _progress[key] = (received, now)


def get_progress(key: tuple[str, str, str]) -> int:
    received, touched = _progress.get(key, (0, 0))
    return received if monotonic() - touched <= 300 else 0


async def upload_file(
    workspace,
    *,
    destination: str,
    name: str,
    size: int,
    kind: str,
    chunks: AsyncIterable[bytes],
    progress=None,
) -> dict:
    transport = ContainerTransport(workspace.id, workspace.directory, workspace.development_tools)
    request = dict(
        workspace=workspace.directory,
        destination=destination,
        name=name,
        size=size,
        kind=kind,
    )
    command = shlex.join(
        [
            "python3",
            "-c",
            load_guest_command("common/files/upload.py"),
            json.dumps(request),
        ]
    )
    process = None
    try:
        async with asyncio.timeout(1800):
            process = await transport.create_process(command, start_if_needed=False)

            async def reply():
                async with asyncio.timeout(60):
                    line = await process.stdout.readline()
                try:
                    data = json.loads(line)
                except ValueError as error:
                    raise ValueError(
                        "The workspace closed the file transfer unexpectedly"
                    ) from error
                if data.get("ok") is False:
                    raise ValueError(data.get("detail") or "Upload failed")
                return data

            if not (await reply()).get("ready"):
                raise ValueError("The workspace did not accept the upload")
            sent = 0
            buffer = bytearray()
            async for chunk in chunks:
                if sent + len(buffer) + len(chunk) > size:
                    raise ValueError("Uploaded file exceeds its declared size")
                view = memoryview(chunk)
                while view:
                    count = min(BLOCK_SIZE - len(buffer), len(view))
                    buffer.extend(view[:count])
                    view = view[count:]
                    if len(buffer) == min(BLOCK_SIZE, size - sent):
                        sent += len(buffer)
                        await process.send_input(bytes(buffer))
                        buffer.clear()
                        if (await reply()).get("received") != sent:
                            raise ValueError("The workspace did not acknowledge uploaded data")
                        if progress:
                            progress(sent)
            if sent != size:
                raise ValueError("Upload interrupted; incomplete file was not saved")
            await process.send_input(None)
            result = await reply()
            if not result.get("ok"):
                raise ValueError("The workspace did not finish saving the file")
            return result
    finally:
        # EOF lets the guest remove its incomplete temporary file on cancellation.
        if process is not None and not process.finished.done():
            try:
                await process.send_input(None)
                await asyncio.wait_for(process.wait(), 3)
            except (Exception, asyncio.CancelledError):
                pass
        await transport.close()
