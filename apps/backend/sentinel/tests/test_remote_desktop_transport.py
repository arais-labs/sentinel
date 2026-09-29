import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.services.runtime.container_transport import ContainerTransport
from app.services.runtime.desktop import RuntimeDesktopManager
from app.services.runtime.workspace import WorkspaceLocation
from app.services.runtime import workspace_containers


@pytest.mark.parametrize("is_remote", [False, True], ids=["local", "ssh"])
@pytest.mark.asyncio
async def test_forward_preserves_binary_bytes_and_half_close(monkeypatch, is_remote):
    # Short private socket path also works on macOS's 104-byte sockaddr_un.
    with tempfile.TemporaryDirectory(prefix="vnc-", dir="/tmp") as root:
        workspace = uuid4()
        path = f"{root}/forwards/{workspace}-5901.sock"
        Path(root, "forwards").mkdir()
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
            workspace_containers,
            "remote_for",
            AsyncMock(return_value=remote if is_remote else None),
        )
        local_request = AsyncMock(
            side_effect=lambda action, **kw: (
                {"root": root} if action == "deployment" else {"socket": path}
            )
        )
        monkeypatch.setattr(workspace_containers, "local_request", local_request)
        transport = ContainerTransport(workspace, "/unused", [])
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
        workspace_containers,
        "remote_for",
        AsyncMock(return_value=remote if is_remote else None),
    )
    monkeypatch.setattr(
        workspace_containers,
        "local_request",
        AsyncMock(
            side_effect=lambda action, **kw: (
                {"root": "/private/runtime"}
                if action == "deployment"
                else {"socket": "/private/another-service.sock"}
            )
        ),
    )
    transport = ContainerTransport(uuid4(), "/unused", [])
    with pytest.raises(RuntimeError, match="invalid desktop socket"):
        await transport.forward_local_port("127.0.0.1", 0, "localhost", 5901)
    with pytest.raises(RuntimeError, match="invalid desktop socket"):
        await transport.desktop_socket(5901)


@pytest.mark.parametrize("is_remote", [False, True], ids=["local", "ssh"])
@pytest.mark.asyncio
async def test_direct_desktop_socket_reuses_tunnel_and_preserves_native_endpoint(
    monkeypatch, is_remote
):
    with tempfile.TemporaryDirectory(prefix="direct-vnc-", dir="/tmp") as root:
        workspace = uuid4()
        path = f"{root}/forwards/{workspace}-5901.sock"
        Path(root, "forwards").mkdir()

        async def echo(reader, writer):
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        native = await asyncio.start_unix_server(echo, path)

        async def forward(local, target):
            assert target == path
            # Real local listener stands in for AsyncSSH's Unix forwarding listener.
            return await asyncio.start_unix_server(echo, local)

        connection = SimpleNamespace(
            forward_local_path=AsyncMock(side_effect=forward),
            is_closed=Mock(return_value=False),
        )
        remote = SimpleNamespace(
            machine=SimpleNamespace(runtime_root=root),
            operation=AsyncMock(return_value={"socket": path}),
            ssh=SimpleNamespace(_ensure_conn=AsyncMock(return_value=connection)),
        )
        monkeypatch.setattr(
            workspace_containers,
            "remote_for",
            AsyncMock(return_value=remote if is_remote else None),
        )
        monkeypatch.setattr(
            workspace_containers,
            "local_request",
            AsyncMock(
                side_effect=lambda action, **kw: (
                    {"root": root} if action == "deployment" else {"socket": path}
                )
            ),
        )
        transport = ContainerTransport(workspace, "/unused", [])
        manager = RuntimeDesktopManager(
            SimpleNamespace(ssh=transport),
            workspace_location=WorkspaceLocation("/unused", "/unused"),
        )
        session = str(uuid4())
        desktop = await manager._connect(
            session, {"display": ":1", "port": 5901, "geometry": "1920x1200"}
        )
        endpoint = desktop.socket_path
        listener = manager._handles[session].listener
        assert desktop.target_port == 5901
        try:
            assert await transport.desktop_socket(5901, listener) == (
                endpoint,
                listener,
            )
            reader, writer = await asyncio.open_unix_connection(endpoint)
            payload = bytes(range(256)) * 1024
            writer.write(payload)
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(len(payload)), 5) == payload
            writer.write_eof()
            assert await asyncio.wait_for(reader.read(), 5) == b""
            writer.close()
            await writer.wait_closed()
            if is_remote:
                connection.forward_local_path.assert_awaited_once()
                assert Path(endpoint).stat().st_mode & 0o777 == 0o600
                assert Path(endpoint).parent.stat().st_mode & 0o777 == 0o700
            else:
                assert endpoint == path and listener is None
        finally:
            if listener:
                listener.close()
                await listener.wait_closed()
                assert not Path(endpoint).exists()
            assert Path(path).exists()
            native.close()
            await native.wait_closed()
