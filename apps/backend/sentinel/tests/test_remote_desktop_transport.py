import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.workspaces.transport.guest import GuestTransport
from app.services.workspaces import lifecycle


@pytest.mark.parametrize("is_remote", [False, True], ids=["local", "ssh"])
@pytest.mark.asyncio
async def test_forward_preserves_binary_bytes_and_half_close(monkeypatch, is_remote):
    # Short private socket path also works on macOS's 104-byte sockaddr_un.
    with tempfile.TemporaryDirectory(prefix="vnc-", dir="/tmp") as root:
        workspace = uuid4()
        # A remote sentineld's state directory is ROOT/state; locally the deployment root is it.
        state = f"{root}/state" if is_remote else root
        path = f"{state}/forwards/{workspace}-5901.sock"
        Path(state, "forwards").mkdir(parents=True)
        payload = bytes(range(256)) * 2048
        finished = asyncio.Event()

        async def echo(reader, writer):
            try:
                data = await reader.read()
                writer.write(data)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                finished.set()

        server = await asyncio.start_unix_server(echo, path)

        async def connect(path, **kwargs):
            return await asyncio.open_unix_connection(path)

        connection = SimpleNamespace(open_unix_connection=AsyncMock(side_effect=connect))
        remote = SimpleNamespace(
            machine=SimpleNamespace(runtime_root=root),
            operation=AsyncMock(return_value={"socket": path}),
            ssh=SimpleNamespace(_ensure_conn=AsyncMock(return_value=connection)),
        )
        monkeypatch.setattr(
            lifecycle,
            "remote_for",
            AsyncMock(return_value=remote if is_remote else None),
        )
        local_request = AsyncMock(
            side_effect=lambda action, **kw: (
                {"root": root} if action == "deployment" else {"socket": path}
            )
        )
        monkeypatch.setattr(lifecycle, "local_request", local_request)
        transport = GuestTransport(workspace, "/unused", [])
        transport.create_process = AsyncMock(side_effect=AssertionError("No JSON process relay"))
        listener = await transport.forward_local_port("127.0.0.1", 0, "127.0.0.1", 5901)
        writer = None
        try:
            port = listener._server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(payload)
            await writer.drain()
            writer.write_eof()
            assert await asyncio.wait_for(reader.readexactly(len(payload)), 5) == payload
            assert await asyncio.wait_for(reader.read(), 5) == b""
            await asyncio.wait_for(finished.wait(), 5)
            if is_remote:
                connection.open_unix_connection.assert_awaited_once_with(path, encoding=None)
                local_request.assert_not_called()
            else:
                connection.open_unix_connection.assert_not_called()
                local_request.assert_any_await("port_forward", workspace=str(workspace), port=5901)
            transport.create_process.assert_not_called()
        finally:
            if writer:
                writer.close()
                await writer.wait_closed()
            listener.close()
            await listener.wait_closed()
            server.close()
            await server.wait_closed()


@pytest.mark.parametrize("is_remote", [False, True], ids=["local", "ssh"])
@pytest.mark.asyncio
async def test_forward_rejects_unexpected_host_socket(monkeypatch, is_remote):
    remote = SimpleNamespace(
        machine=SimpleNamespace(runtime_root="/private/runtime"),
        operation=AsyncMock(return_value={"socket": "/private/another-service.sock"}),
    )
    monkeypatch.setattr(
        lifecycle,
        "remote_for",
        AsyncMock(return_value=remote if is_remote else None),
    )
    monkeypatch.setattr(
        lifecycle,
        "local_request",
        AsyncMock(
            side_effect=lambda action, **kw: (
                {"root": "/private/runtime"}
                if action == "deployment"
                else {"socket": "/private/another-service.sock"}
            )
        ),
    )
    transport = GuestTransport(uuid4(), "/unused", [])
    with pytest.raises(RuntimeError, match="invalid forward socket"):
        await transport.forward_local_port("127.0.0.1", 0, "localhost", 5901)
