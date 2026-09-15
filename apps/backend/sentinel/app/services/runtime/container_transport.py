"""Processes inside a workspace VM, carried over the desktop's private socket."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import tempfile
from uuid import UUID

from websockets.asyncio.client import unix_connect

from app.config import settings
from app.schemas.runtime import RuntimeExecResult
from app.services.runtime import workspace_containers as containers
from app.services.runtime.local_transport import _LocalPortForward, _StreamReader
from app.services.runtime.ssh_client import build_shell_command


class _DesktopSocketForward:
    def __init__(self, directory, listener, connection, remote_path):
        self.directory = directory
        self.path = directory + "/desktop.sock"
        self.listener = listener
        self.connection = connection
        self.remote_path = remote_path
        self.closed = False

    def close(self):
        self.closed = True
        self.listener.close()

    async def wait_closed(self):
        try:
            await self.listener.wait_closed()
        finally:
            shutil.rmtree(self.directory, ignore_errors=True)


class ContainerProcess:
    def __init__(self, socket, encoding):
        self.socket = socket
        self.reader = asyncio.StreamReader(limit=4 * 1024 * 1024)
        self.stdout = _StreamReader(self.reader, encoding)
        self.stderr = None
        self.stdin = self
        self.exit_status = None
        self.started = asyncio.get_running_loop().create_future()
        self.finished = asyncio.get_running_loop().create_future()
        self.queue = asyncio.Queue(maxsize=256)
        self.receiver = asyncio.create_task(self._receive())
        self.sender = asyncio.create_task(self._send())

    async def _send(self):
        try:
            while True:
                await self.socket.send(json.dumps(await self.queue.get()))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.socket.close()
            self.reader.set_exception(exc)

    async def _receive(self):
        try:
            async for raw in self.socket:
                event = json.loads(raw)
                if event["event"] == "started" and not self.started.done():
                    self.started.set_result(None)
                elif event["event"] == "output":
                    self.reader.feed_data(base64.b64decode(event["data"]))
                    # Backpressure also bounds a disconnected/slow UI's memory.
                    if len(self.reader._buffer) > 8 * 1024 * 1024:
                        raise RuntimeError("Terminal output consumer is too slow")
                elif event["event"] == "exit":
                    if event.get("error"):
                        raise RuntimeError(event["error"])
                    self.exit_status = event.get("exitCode", 0)
                    break
        except Exception as exc:
            self.reader.set_exception(exc)
            if not self.started.done():
                self.started.set_exception(exc)
        finally:
            if not self.started.done():
                self.started.set_exception(RuntimeError("Workspace process closed before starting"))
            self.reader.feed_eof()
            self.sender.cancel()
            await self.socket.close()
            if not self.finished.done():
                self.finished.set_result(self.exit_status)

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        self.queue.put_nowait({"action": "input", "data": base64.b64encode(data).decode()})

    def write_eof(self):
        self.queue.put_nowait({"action": "input"})

    async def send_input(self, data: bytes | None):
        """Await socket flow control for a producer that streams file contents."""
        message = {"action": "input"}
        if data is not None:
            message["data"] = base64.b64encode(data).decode()
        await self.socket.send(json.dumps(message))

    def change_terminal_size(self, cols, rows):
        self.queue.put_nowait({"action": "resize", "cols": cols, "rows": rows})

    def terminate(self):
        if not self.finished.done():
            asyncio.create_task(self.socket.close())

    async def wait(self):
        return await asyncio.shield(self.finished)


class ContainerTransport:
    def __init__(self, workspace_id: UUID, directory: str, tools: list[str]):
        self.workspace_id = workspace_id
        self.directory = directory
        self.tools = tools
        self.processes: set[ContainerProcess] = set()

    async def workspace_status(self) -> dict:
        async with asyncio.timeout(3):
            return (await containers.statuses(self.workspace_id)).get(
                str(self.workspace_id), {"state": "stopped"}
            )

    async def is_ready(self) -> bool:
        # Read-only UI queries must not launch or wait for workspace setup.
        try:
            async with asyncio.timeout(3):
                state = (await containers.statuses(self.workspace_id)).get(
                    str(self.workspace_id), {}
                )
                return state.get("state") == "running"
        except (TimeoutError, containers.WorkspaceContainerError):
            return False

    async def wait_ready(self, *, timeout=900, retry=False):
        await containers.ensure_ready(
            self.workspace_id, self.directory, self.tools, timeout=timeout, retry=retry
        )

    async def run(self, command, *, timeout=300, cwd=None, env=None):
        await self.wait_ready()
        reply = await containers.request(
            "exec",
            workspace=str(self.workspace_id),
            arguments=["bash", "-c", build_shell_command(command, cwd=cwd, env=env)],
            timeout=timeout,
        )
        return RuntimeExecResult(
            exit_status=reply["exitCode"],
            stdout=reply.get("stdout", ""),
            stderr=reply.get("stderr", ""),
        )

    async def run_script(self, script, *, args=None, timeout=300):
        await self.wait_ready()
        reply = await containers.request(
            "exec",
            workspace=str(self.workspace_id),
            arguments=["bash", "-c", script, "sentinel", *(args or [])],
            timeout=timeout,
        )
        return RuntimeExecResult(
            exit_status=reply["exitCode"],
            stdout=reply.get("stdout", ""),
            stderr=reply.get("stderr", ""),
        )

    async def create_process(
        self,
        command,
        *,
        term_type=None,
        term_size=(80, 24),
        encoding=None,
        start_if_needed=True,
    ):
        if start_if_needed:
            await self.wait_ready()
        elif not await self.is_ready():
            raise containers.WorkspaceContainerError(
                "Start this workspace before accessing its files."
            )
        remote = await containers.remote_for(self.workspace_id)
        if remote:
            await remote.connect()
        socket = await unix_connect(
            remote.bridge_socket if remote else settings.workspace_runtime_socket,
            uri="ws://workspace-runtime/v1/process",
            additional_headers={"x-sentinel-desktop-token": settings.sentinel_desktop_token},
            max_size=1024 * 1024,
        )
        process = ContainerProcess(socket, encoding)
        self.processes.add(process)
        process.finished.add_done_callback(lambda _: self.processes.discard(process))
        await socket.send(
            json.dumps(
                {
                    "action": "start",
                    "workspace": str(self.workspace_id),
                    "arguments": ["bash", "-c", command],
                    "terminal": bool(term_type),
                    "cols": term_size[0],
                    "rows": term_size[1],
                }
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(process.started), 60)
        except BaseException:
            process.terminate()
            await process.wait()
            raise
        return process

    async def _guest_forward(self, target_host, target_port):
        # socat connects to guest loopback. No VM or host engine socket is exposed.
        if target_host not in {"localhost", "127.0.0.1", "::1"} or not 1 <= target_port <= 65535:
            raise ValueError("Expected a guest loopback port")

        remote = await containers.remote_for(self.workspace_id)
        # One native guest forward and one byte-copy loop for every VM. Only
        # opening the owning host's private socket differs (direct vs SSH).
        if remote:
            request = remote.operation
            root = remote.machine.runtime_root

            async def connect(path):
                connection = await remote.ssh._ensure_conn()
                return await connection.open_unix_connection(path, encoding=None)

        else:
            request = containers.local_request
            root = (await containers.local_request("deployment", prepare=False))["root"]
            connect = asyncio.open_unix_connection

        reply = await request("port_forward", workspace=str(self.workspace_id), port=target_port)
        expected = f"{root}/forwards/{self.workspace_id}-{target_port}.sock"
        if len(expected.encode()) >= 104:
            digest = hashlib.sha256(root.encode()).hexdigest()[:16]
            expected = f"/tmp/sentinel-forward-{digest}/{self.workspace_id}-{target_port}.sock"
        if reply.get("socket") != expected:
            raise RuntimeError("Runtime returned an invalid desktop socket")

        return expected, remote, connect

    async def desktop_socket(self, port, existing=None):
        """Resolve a private desktop stream; Electron never owns workspace/SSH routing."""
        expected, remote, _ = await self._guest_forward("127.0.0.1", port)
        if not remote:
            return expected, None
        connection = await remote.ssh._ensure_conn()
        if (
            existing is not None
            and not existing.closed
            and existing.connection is connection
            and not connection.is_closed()
            and existing.remote_path == expected
        ):
            return existing.path, existing
        directory = tempfile.mkdtemp(prefix="sentinel-desktop-", dir="/tmp")
        try:
            listener = await connection.forward_local_path(directory + "/desktop.sock", expected)
            os.chmod(directory + "/desktop.sock", 0o600)
        except BaseException:
            if "listener" in locals():
                listener.close()
                await listener.wait_closed()
            shutil.rmtree(directory, ignore_errors=True)
            raise
        forward = _DesktopSocketForward(directory, listener, connection, expected)
        return forward.path, forward

    async def forward_local_port(self, listen_host, listen_port, target_host, target_port):
        expected, _, connect = await self._guest_forward(target_host, target_port)

        async def handle_binary(reader, writer):
            upstream = None
            tasks = []
            try:
                source, upstream = await connect(expected)

                async def copy(source, destination, *, send_eof=False):
                    while data := await source.read(65536):
                        destination.write(data)
                        await destination.drain()
                    if send_eof:
                        destination.write_eof()

                tasks = [
                    asyncio.create_task(copy(reader, upstream, send_eof=True)),
                    asyncio.create_task(copy(source, writer)),
                ]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
                if tasks[1] not in done:
                    # A caller can finish sending and still expect a reply.
                    await tasks[1]
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if upstream:
                    upstream.close()
                    await upstream.wait_closed()
                writer.close()
                await writer.wait_closed()

        return _LocalPortForward(
            await asyncio.start_server(handle_binary, listen_host, listen_port)
        )

    async def close(self):
        processes = list(self.processes)
        for process in processes:
            process.terminate()
        await asyncio.gather(*(process.wait() for process in processes))


class RunningContainerTransport(ContainerTransport):
    async def wait_ready(self, *, timeout=900, retry=False):
        # Recheck before each command, including when the runtime stops mid-cleanup.
        if not await self.is_ready():
            raise containers.WorkspaceContainerError("Workspace is offline; cleanup deferred")
