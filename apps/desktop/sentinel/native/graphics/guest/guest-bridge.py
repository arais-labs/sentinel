"""Multiplex workspace-local graphics sockets over the owned VM process stream."""

import asyncio
import os
import struct
from pathlib import Path

HEADER = struct.Struct("!BII")
LIMIT = 32768
clients = {}
sequence = 0


def emit(kind, client=0, data=b""):
    frame = HEADER.pack(kind, client, len(data)) + data
    while frame:
        size = os.write(1, frame)
        frame = frame[size:]


async def connected(reader, writer):
    global sequence
    if len(clients) >= 16:
        writer.close()
        return
    sequence += 1
    client = sequence
    clients[client] = writer
    emit(1, client)
    try:
        while data := await reader.read(LIMIT):
            emit(2, client, data)
    except ConnectionError:
        pass
    finally:
        clients.pop(client, None)
        writer.close()
        emit(3, client)


async def main():
    log = os.open("/var/log/sentinel-graphics.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(log, 2)
    os.close(log)
    root = Path("/run/sentinel-graphics")
    root.mkdir(mode=0o700, exist_ok=True)
    sock = root / "renderer.sock"
    sock.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(connected, str(sock))
    sock.chmod(0o600)
    reader = asyncio.StreamReader(limit=LIMIT * 2)
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(0, "rb", buffering=0)
    )
    emit(4)
    try:
        while True:
            kind, client, length = HEADER.unpack(await reader.readexactly(HEADER.size))
            if length > LIMIT or kind not in (2, 3):
                raise ValueError("Invalid graphics frame")
            data = await reader.readexactly(length)
            writer = clients.get(client)
            if writer:
                if kind == 3:
                    writer.close()
                else:
                    try:
                        writer.write(data)
                        await writer.drain()
                    except ConnectionError:
                        writer.close()
    except asyncio.IncompleteReadError:
        pass
    finally:
        server.close()
        await server.wait_closed()
        for writer in list(clients.values()):
            writer.close()
        sock.unlink(missing_ok=True)


asyncio.run(main())
