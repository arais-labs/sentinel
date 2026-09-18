"""Sentinel's signed Mac VM helper, controlled over pinned SSH and a private socket.

The service owns VMs independently of SSH. Requests are never replayed after an
ambiguous disconnect. Installation is a separate, explicitly approved operation.
"""

from __future__ import annotations

import app.services.runtime.remote_update as remote_update
import app.services.runtime.workspace_containers as workspace_containers

import asyncio
import dataclasses
import hashlib
import json
import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from shlex import quote
from uuid import uuid4

import asyncssh
import httpx

import app.database as database_module
import app.services.runtime.machines as machines_module
from app.config import settings
from app.services.runtime.ssh_client import SSHClient


class RemoteMacError(RuntimeError):
    pass


class RuntimeUnavailable(RemoteMacError):
    pass


def runtime_assets(assets):
    """All host-side assets are versioned and verified as one remote release."""
    executable = Path(assets["executable"])
    files = [
        (executable, "sentinel-workspace-runtime"),
        (Path(assets["kernel"]), "kernel"),
    ]
    graphics = executable.parent / "graphics"
    for name in (
        "sentinel-graphics-renderer",
        "libEGL.dylib",
        "libGLESv2.dylib",
        "libepoxy.0.dylib",
        "libvirglrenderer.1.dylib",
        "guest-bridge.py",
        "install-guest.py",
        "mesa-linux-arm64.tar.xz",
        "mesa-linux-arm64.json",
        "mesa-linux-arm64-glibc.tar.xz",
        "mesa-linux-arm64-glibc.json",
    ):
        files.append((graphics / name, "graphics/" + name))
    return files


@lru_cache(maxsize=8)
def _available_version(files, init_image, workspace_image):
    hashes = [hashlib.sha256(Path(file[0]).read_bytes()).hexdigest() for file in files]
    return hashlib.sha256(json.dumps([*hashes, init_image, workspace_image]).encode()).hexdigest()[
        :16
    ]


async def available_version():
    """Compare shipped assets without preparing/downloading or booting a VM."""

    try:
        assets = await workspace_containers.local_request("deployment", prepare=False)
        files = []
        for path, _ in runtime_assets(assets):
            stat = path.stat()
            files.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        return await asyncio.to_thread(
            _available_version,
            tuple(files),
            assets["initImage"],
            assets["workspaceImage"],
        )
    except Exception:
        # Missing local assets / desktop bridge are unknown, never "up to date".
        return None


async def wait_for_runtime_ready(
    conn, relay, *, socket_timeout=30, startup_timeout=1800, retry_interval=0.25
):
    """Wait for transport availability, then the service's readiness handshake.

    Only a failed socket connect is retryable. Never restart the service or replay
    requests, and never hide an initialization failure behind another probe.
    """
    connected = False
    try:
        async with asyncio.timeout(socket_timeout) as deadline:
            while True:
                probe = await conn.create_process(relay, encoding="utf-8")
                try:
                    while line := await probe.stdout.readline():
                        event = json.loads(line)
                        kind = event.get("event")
                        # Older installed helpers used this exact fatal message
                        # for connect failure. Accept it only before a handshake.
                        unavailable = kind == "unavailable" or (
                            kind == "fatal"
                            and event.get("error") == "Remote runtime is not running"
                        )
                        if unavailable and not connected:
                            break
                        if kind == "fatal" or unavailable:
                            raise RemoteMacError(event.get("error") or "Runtime startup failed")
                        if kind not in {"connected", "preparing", "ready"}:
                            raise RemoteMacError("Unexpected runtime startup response")
                        if not connected:
                            connected = True
                            deadline.reschedule(asyncio.get_running_loop().time() + startup_timeout)
                        if kind == "ready":
                            return
                    else:
                        detail = (await probe.stderr.read())[-2000:].strip()
                        raise RemoteMacError(
                            "Remote runtime connection closed before readiness"
                            + (f": {detail}" if detail else "")
                        )
                finally:
                    probe.close()
                await asyncio.sleep(retry_interval)
    except TimeoutError as exc:
        phase = "initialization" if connected else "control socket"
        raise RemoteMacError(f"Timed out waiting for remote runtime {phase}") from exc


async def fingerprint(machine: machines_module.ResolvedMachine) -> dict:
    key = await asyncssh.get_server_host_key(machine.host, port=machine.port)
    if key is None:
        raise RemoteMacError("The SSH server did not provide a host key")
    return {
        "host_key": key.export_public_key().decode().strip(),
        "fingerprint": key.get_fingerprint(),
    }


async def inspect_installation(machine):
    """Read durable worker state without starting the service."""
    client = SSHClient(machine.credentials())
    try:
        return await read_installation(await client._ensure_conn(), machine.runtime_root)
    finally:
        await client.close()


async def read_installation(conn, root):
    if not root:
        home = await conn.run('printf "%s\\n" "$HOME"', check=True, timeout=10)
        home = home.stdout.rstrip("\n")
        if not home.startswith("/") or any(char in home for char in ("\0", "\n", "\r")):
            raise RemoteMacError("SSH returned an invalid home directory")
        root = home.rstrip("/") + "/.sentinel/runtime"
    async with conn.start_sftp_client() as sftp:

        async def read(name):
            try:
                async with sftp.open(root + "/" + name) as file:
                    return json.loads(await file.read())
            except asyncssh.SFTPNoSuchFile:
                return {}

        manifest = await read("manifest.json")
        journal = await read("update.json")
        catalog = await read("workspaces.json")
        return {
            "installed": bool(manifest),
            "path": root,
            "installed_version": manifest.get("version"),
            "worker_id": catalog.get("worker_id"),
            "update_phase": journal.get("phase"),
            "update_error": journal.get("error"),
            "update_warning": journal.get("warning"),
        }


class UpdateApprovalRequired(RemoteMacError):
    def __init__(self, workspaces):
        self.workspaces = workspaces
        super().__init__("Approve stopping and restarting running workspaces to update the runtime")


async def _running_workspaces(conn, root):
    """Read the current service without preparing assets or starting a VM.

    A broken installed helper must still be repairable. None means the final
    check with the replacement helper must determine the affected workspaces.
    """
    try:
        async with asyncio.timeout(10):
            async with conn.start_sftp_client() as sftp:
                try:
                    async with sftp.open(root + "/manifest.json") as file:
                        manifest = json.loads(await file.read())
                except asyncssh.SFTPNoSuchFile:
                    return []
            result = await service_request(conn, manifest, root, "status")
            return [id for id, state in result["states"].items() if state == "running"]
    except RuntimeUnavailable:
        return []
    except (
        RemoteMacError,
        TimeoutError,
        OSError,
        asyncssh.Error,
        ValueError,
        KeyError,
        TypeError,
    ):
        return None


async def plan_installation(machine):
    check_maintenance(machine)
    if not machine.runtime_root or not machine.host_key:
        return []
    client = SSHClient(machine.credentials())
    try:
        return await _running_workspaces(await client._ensure_conn(), machine.runtime_root)
    finally:
        await client.close()


async def service_request(conn, manifest, root, action, **values):
    """One RPC; never replay a mutation after a lost response."""

    request_id = str(uuid4())
    relay = " ".join(map(quote, [manifest["executable"], "--relay", root + "/control.sock"]))
    process = await conn.create_process(relay, encoding="utf-8")
    try:
        sent = False
        async with asyncio.timeout(5 if action == "status" else 120):
            while line := await process.stdout.readline():
                result = json.loads(line)
                if result.get("event") == "unavailable" or (
                    result.get("event") == "fatal"
                    and result.get("error") == "Remote runtime is not running"
                ):
                    raise RuntimeUnavailable("Remote runtime is not running")
                if result.get("event") == "fatal":
                    raise RemoteMacError(result.get("error") or "Remote runtime failed")
                # Consume the ready frame before issuing a request. Closing a
                # probe without reading it can SIGPIPE legacy services.
                if result.get("event") == "ready" and not result.get("id") and not sent:
                    process.stdin.write(
                        json.dumps({"id": request_id, "action": action, **values}) + "\n"
                    )
                    sent = True
                if result.get("id") == request_id:
                    if result.get("error"):
                        raise RemoteMacError(result["error"])
                    if action == "maintenance":
                        # EOF means the old owner has released its store lock.
                        await process.stdout.read()
                    return result
        raise RemoteMacError("Runtime connection closed without confirming the operation")
    finally:
        process.close()


_maintenance = {}
install_progress = {}


def check_maintenance(machine):
    owner = _maintenance.get(str(machine.id))
    if owner is not None and owner is not asyncio.current_task():
        raise RemoteMacError("Runtime update in progress; try again when it finishes")


async def install(
    machine: machines_module.ResolvedMachine,
    host_key: str,
    assets: dict,
    approved_workspaces=None,
    *,
    reinstall=False,
) -> dict:
    check_maintenance(machine)
    _maintenance[str(machine.id)] = asyncio.current_task()
    install_progress[str(machine.id)] = "Preparing runtime assets"
    try:
        return await _install(machine, host_key, assets, approved_workspaces, reinstall=reinstall)
    finally:
        _maintenance.pop(str(machine.id), None)
        install_progress.pop(str(machine.id), None)


async def _install(
    machine: machines_module.ResolvedMachine,
    host_key: str,
    assets: dict,
    approved_workspaces=None,
    *,
    reinstall=False,
) -> dict:
    client = SSHClient(dataclasses.replace(machine.credentials(), host_key=host_key))
    try:
        conn = await client._ensure_conn()
        info = await conn.run(
            "/usr/bin/uname -sm; /usr/bin/sw_vers -productVersion; printf '%s\\n' \"$HOME\"",
            check=True,
            timeout=15,
        )
        lines = info.stdout.splitlines()
        if len(lines) < 3 or lines[0] != "Darwin arm64" or int(lines[1].split(".")[0]) < 26:
            raise RemoteMacError(
                "Remote workspaces require an Apple Silicon Mac with macOS 26 or newer"
            )
        home = lines[2]
        if not home.startswith("/") or "\0" in home:
            raise RemoteMacError("SSH returned an invalid home directory")
        root = home + "/.sentinel/runtime"
        # Ask before hashing/uploading the bundle. remote_update.apply_update repeats this
        # check under the remote update lease before stopping anything.
        running = await _running_workspaces(conn, root)
        if running is not None and not set(running).issubset(approved_workspaces or []):
            raise UpdateApprovalRequired(running)
        files = runtime_assets(assets)
        hashes = await asyncio.gather(
            *(
                asyncio.to_thread(lambda path=path: hashlib.sha256(path.read_bytes()).hexdigest())
                for path, _ in files
            )
        )
        version = hashlib.sha256(
            json.dumps([*hashes, assets["initImage"], assets["workspaceImage"]]).encode()
        ).hexdigest()[:16]

        # Each staging area is private to this attempt. Concurrent desktops must
        # never overwrite an executable another updater has already verified.
        release = root + "/releases/" + version + "-" + uuid4().hex
        config = {
            "host_key": host_key,
            "runtime_root": root,
            "runtime_version": version,
        }

        def progress(phase):
            install_progress[str(machine.id)] = phase.replace("_", " ").capitalize()

        progress("Uploading and verifying runtime assets")
        await conn.run(f"umask 077; mkdir -p {quote(release + '/graphics')}", check=True)
        async with conn.start_sftp_client() as sftp:
            for (source, name), expected in zip(files, hashes):
                remote = release + "/" + name

                def transferred(src, dst, copied, total):
                    percent = int(copied * 100 / total) if total else 100
                    progress(f"Uploading {name}: {percent}%")

                await sftp.put(str(source), remote + ".partial", progress_handler=transferred)
                result = await conn.run(
                    f"/usr/bin/shasum -a 256 {quote(remote + '.partial')}", check=True
                )
                if result.stdout.split()[0] != expected:
                    raise RemoteMacError("Transferred runtime asset checksum does not match")
                await sftp.posix_rename(remote + ".partial", remote)
            await sftp.chmod(release + "/sentinel-workspace-runtime", 0o700)
            await sftp.chmod(release + "/graphics/sentinel-graphics-renderer", 0o700)
        progress("Verifying runtime signature")
        await conn.run(
            f"/usr/bin/codesign --verify --strict {quote(release + '/sentinel-workspace-runtime')}",
            check=True,
        )
        await conn.run(
            f"/usr/bin/codesign --verify --strict {quote(release + '/graphics/sentinel-graphics-renderer')}",
            check=True,
        )
        manifest = {
            "executable": release + "/sentinel-workspace-runtime",
            "kernel": release + "/kernel",
            "initImage": assets["initImage"],
            "workspaceImage": assets["workspaceImage"],
            "version": version,
            "updateProtocol": 1,
            "storeVersion": 1,
            "graphicsProtocol": 1,
        }
        progress("Acquiring remote update lock")
        async with remote_update.UpdateSession(conn, manifest["executable"], root) as session:
            from app.services.runtime import migration_inputs

            manifest["runtimeMigrations"], inputs = await migration_inputs.prepare(
                session, machine.id
            )
            runtime = RemoteMacRuntime(
                dataclasses.replace(machine, host_key=host_key, runtime_root=root)
            )
            runtime.maintenance_owner = asyncio.current_task()
            # Always use the new relay to talk to either version. No unsafe ping
            # and no dependency on the old executable being runnable.
            transport = {"executable": manifest["executable"]}

            async def snapshot(current):
                try:
                    return await service_request(conn, transport, root, "status")
                except (RemoteMacError, TimeoutError) as exc:
                    raise RemoteMacError(
                        "The runtime owns workspace storage but is not responding. "
                        f"Restart {machine.name}, then reopen Machines and retry this update. "
                        "Restarting the machine stops its running commands. Workspace files are kept."
                    ) from exc

            async def stop(current, running):
                cached = runtimes.pop(str(machine.id), None)
                if cached:
                    await cached.close()
                await service_request(
                    conn, transport, root, "maintenance", approved_workspaces=running
                )
                await runtime.close()

            async def validate(current):
                relay = " ".join(
                    map(
                        quote,
                        [manifest["executable"], "--relay", root + "/control.sock"],
                    )
                )
                await wait_for_runtime_ready(conn, relay)
                await service_request(conn, transport, root, "status")

            async def resume(ids):
                await runtime.operation("workspace_resume", approved_workspaces=ids)

            try:
                await remote_update.apply_update(
                    session,
                    manifest,
                    snapshot=snapshot,
                    stop=stop,
                    validate=validate,
                    resume=resume,
                    approved=approved_workspaces or [],
                    progress=progress,
                    reinstall=reinstall,
                    migration_inputs=inputs,
                )
                cached = runtimes.pop(str(machine.id), None)
                if cached:
                    await cached.close()
            finally:
                await runtime.close()
        return config
    finally:
        await client.close()


class RemoteMacRuntime:
    def __init__(self, machine):
        if not machine.host_key:
            raise RemoteMacError("Verify this machine's SSH identity before connecting")
        self.machine = machine
        self.ssh = SSHClient(machine.credentials())
        self.lock = asyncio.Lock()
        self.listener = None
        self.directory = None
        self.bridge_socket = None
        self.executable = None
        self.connection_task = None
        self.maintenance_owner = None

    async def connect(self):
        if self.maintenance_owner is not asyncio.current_task():
            check_maintenance(self.machine)
        if self.connection_task is None or self.connection_task.done():
            self.connection_task = asyncio.create_task(self._connect())
            self.connection_task.add_done_callback(
                lambda task: None if task.cancelled() else task.exception()
            )
        await asyncio.shield(self.connection_task)

    async def _connect(self):

        async with self.lock:
            # _connect runs in a shielded child task, not the install caller.
            updating = (
                self.maintenance_owner is not None
                and _maintenance.get(str(self.machine.id)) is self.maintenance_owner
            )
            if self.ssh._conn and not self.ssh._conn.is_closed() and self.listener:
                # The operation itself checks the service. A fresh SSH relay and
                # status request before every operation doubles remote round trips.
                return
            await self.ssh.close()
            conn = await self.ssh._ensure_conn()
            installation = await read_installation(conn, self.machine.runtime_root)
            if not installation["installed"]:
                raise RemoteMacError("Sentinel Runtime is not installed on this worker")
            root = installation["path"]
            if self.machine.worker_id and installation.get("worker_id") != self.machine.worker_id:
                raise RemoteMacError(
                    "This account's worker identity changed. Verify the worker before reconnecting."
                )
            self.machine = dataclasses.replace(self.machine, runtime_root=root)
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(root + "/manifest.json") as file:
                    manifest = json.loads(await file.read())
                try:
                    async with sftp.open(root + "/update.json") as file:
                        journal = json.loads(await file.read())
                except asyncssh.SFTPNoSuchFile:
                    journal = {}
            if journal.get("phase") not in (None, "complete", "rolled_back") and not updating:
                raise RemoteMacError(
                    "Runtime update is pending recovery. Open this machine's Runtime dialog to continue."
                )
            exe = manifest["executable"]
            self.executable = exe
            args = [
                exe,
                "--service",
                root,
                manifest["kernel"],
                manifest["initImage"],
                manifest["workspaceImage"],
            ]
            # A private store lock rejects duplicate owners. No boot/login service.
            # Do not replay mutations after a dropped connection.
            try:
                await service_request(conn, manifest, root, "status")
            except RuntimeUnavailable:
                if updating:
                    raise RemoteMacError("The update coordinator has not started a ready runtime")
                await conn.run(
                    f"/usr/bin/nohup {' '.join(map(quote, args))} </dev/null >>{quote(root + '/service.log')} 2>&1 &",
                    check=True,
                    timeout=15,
                )
            relay = " ".join(map(quote, [exe, "--relay", root + "/control.sock"]))
            await wait_for_runtime_ready(conn, relay)
            old_directory = self.directory
            self.directory = tempfile.mkdtemp(prefix="sentinel-remote-", dir="/tmp")
            os.chmod(self.directory, 0o700)
            socket = self.directory + "/control.sock"
            self.listener = await conn.forward_local_path(socket, root + "/control.sock")
            try:
                result = await workspace_containers.local_request(
                    "remote_register",
                    machine=str(self.machine.id),
                    socket=socket,
                    bridge=self.directory + "/bridge.sock",
                )
                self.bridge_socket = result["socket"]
            except BaseException:
                self.listener.close()
                self.listener = None
                raise
            finally:
                if old_directory:

                    shutil.rmtree(old_directory, ignore_errors=True)

    async def operation(self, action, **values):

        await self.connect()
        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(uds=self.bridge_socket),
            base_url="http://remote-runtime",
            timeout=1900,
            trust_env=False,
            headers={"x-sentinel-desktop-token": settings.sentinel_desktop_token},
        ) as client:
            try:
                response = await client.post("/v1/request", json={"action": action, **values})
            except httpx.TransportError:
                # Rebuild on the next request, never replay a possibly executed
                # command when its response was lost.
                if self.listener:
                    self.listener.close()
                    self.listener = None
                raise
            result = response.json()
            if response.is_error or result.get("error"):
                raise RemoteMacError(result.get("error", "Remote workspace request failed"))
            return result

    async def overview(self):
        return await self.operation("status")

    async def close(self):
        if self.connection_task and not self.connection_task.done():
            self.connection_task.cancel()
            await asyncio.gather(self.connection_task, return_exceptions=True)
        # Closing the forwarded connection does not shut down the remote service.
        if self.listener:
            self.listener.close()
        await self.ssh.close()
        if self.directory:

            shutil.rmtree(self.directory, ignore_errors=True)


runtimes = {}
_runtime_lock = asyncio.Lock()


async def get_runtime(machine_id):

    async with database_module.ManagerSessionLocal() as db:
        machine = machines_module.resolve_machine_secret(
            await machines_module.get_machine(db, machine_id)
        )
    async with _runtime_lock:
        existing = runtimes.get(str(machine_id))
        if existing and existing.machine.updated_at_marker != machine.updated_at_marker:
            await existing.close()
            existing = None
        if existing is None:
            existing = RemoteMacRuntime(machine)
            runtimes[str(machine_id)] = existing
        return existing


async def close_all():
    await asyncio.gather(
        *(runtime.close() for runtime in runtimes.values()), return_exceptions=True
    )
    runtimes.clear()
