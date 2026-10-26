"""A remote Mac's sentineld, controlled over pinned SSH and its private socket.

The service owns VMs independently of SSH. Requests are never replayed after an
ambiguous disconnect. Installation is a separate, explicitly approved operation.
"""

from __future__ import annotations

import app.services.workspaces.machines.remote.update as remote_update
import app.services.workspaces.lifecycle as workspace_containers

import asyncio
import dataclasses
import hashlib
import json
import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path, PurePosixPath
from shlex import quote
from uuid import uuid4

import asyncssh
import httpx

import app.database as database_module
import app.services.workspaces.machines.registry as machines_module
from app.config import settings
from app.services.workspaces.machines.remote.ssh import SSHClient
from app.services.workspaces.compatibility import RuntimeCompatibilityError
from app.services.workspaces.machines.remote.release import (
    SIGNED,
    release_files,
    runtime_version,
    validate_release_files,
)

# Where sentineld is installed on the remote Mac.
RUNTIME_DIRECTORY = ".sentinel/sentineld"
_EXECUTABLE = "/sentineld/bin/sentineld"


class RemoteMacError(RuntimeError):
    pass


class RuntimeUnavailable(RemoteMacError):
    pass


def unreachable(machine, exc: BaseException) -> RuntimeUnavailable:
    """A connection failure worded for people: which machine, and what to check."""
    where = f"{machine.name} ({machine.host})"
    if isinstance(exc, ConnectionRefusedError):
        reason = "it refused the connection. Check that it is on and Remote Login is enabled"
    elif isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        reason = "it did not answer. Check that it is on and on this network"
    elif isinstance(exc, asyncssh.PermissionDenied):
        reason = "it refused Sentinel's SSH key"
    elif isinstance(exc, asyncssh.HostKeyNotVerifiable):
        reason = "its host key changed. Verify the machine before reconnecting"
    else:
        reason = str(exc) or type(exc).__name__
    return RuntimeUnavailable(f"Can't reach {where}: {reason}.")


def runtime_assets(assets, *, installed=False):
    """[(path, release name)] of one release.

    Local: the sentineld build tree the desktop app runs. Installed: the manifest's recorded
    inventory under its release directory, validated, never discovered.
    """
    if not installed:
        return release_files(assets["sentineld"])
    executable = assets["executable"]
    if not isinstance(executable, str) or not executable.endswith(_EXECUTABLE):
        raise ValueError("Invalid release executable")
    release = PurePosixPath(executable[: -len(_EXECUTABLE)])
    names = validate_release_files(assets.get("files"))
    return [(Path(release / name), name) for name in names]


@lru_cache(maxsize=8)
def _available_version(files):
    hashes = [hashlib.sha256(Path(file[0]).read_bytes()).hexdigest() for file in files]
    return runtime_version([file[1] for file in files], hashes)


async def available_version():
    """Compare shipped assets without preparing/downloading or booting a VM."""

    try:
        assets = await workspace_containers.local_request("deployment", prepare=False)
        files = []
        for path, name in runtime_assets(assets):
            stat = path.stat()
            files.append((str(path), name, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        return await asyncio.to_thread(_available_version, tuple(files))
    except Exception:
        # Missing local assets / desktop bridge are unknown, never "up to date".
        return None


def _socket(root):
    return root + "/state/sentineld.sock"


async def _control(conn, root):
    """A control connection to the remote sentineld (DESIGN.md §4), handshake done.

    The SSH server opens the Unix socket itself: no helper process on the Mac. A refused
    connection means no sentineld is listening, the only state a caller may retry or start from.
    """
    try:
        reader, writer = await conn.open_unix_connection(_socket(root), encoding="utf-8")
    except (asyncssh.ChannelOpenError, OSError) as exc:
        raise RuntimeUnavailable("Remote runtime is not running") from exc
    serial = 0

    async def call(op, **values):
        nonlocal serial
        serial += 1
        writer.write(json.dumps({"id": serial, "op": op, **values}) + "\n")
        while line := await reader.readline():
            reply = json.loads(line)
            if reply.get("id") != serial:
                continue  # workspace events
            if reply.get("error"):
                error = reply["error"]
                raise RemoteMacError(
                    error.get("message") if isinstance(error, dict) else str(error)
                )
            return reply
        raise RemoteMacError("Runtime connection closed without confirming the operation")

    try:
        await call("status", api=1)
    except BaseException:
        writer.close()
        raise
    return reader, writer, call


async def wait_for_runtime_ready(conn, root, *, timeout=60, retry_interval=0.25):
    """Wait until sentineld answers on its socket; it listens once its state is loaded.

    Only a refused connection is retried. Never restart the service or replay requests.
    """
    try:
        async with asyncio.timeout(timeout):
            while True:
                try:
                    _, writer, _ = await _control(conn, root)
                    writer.close()
                    return
                except RuntimeUnavailable:
                    await asyncio.sleep(retry_interval)
    except TimeoutError as exc:
        log = await conn.run(
            f"/usr/bin/tail -n 12 {quote(root + '/service.log')}", check=False, timeout=10
        )
        detail = (log.stdout or "").strip()[-2000:]
        raise RemoteMacError(
            "Timed out waiting for the remote runtime" + (f": {detail}" if detail else "")
        ) from exc


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
        root = home.rstrip("/") + "/" + RUNTIME_DIRECTORY
    async with conn.start_sftp_client() as sftp:

        async def read(name, parse=json.loads):
            try:
                async with sftp.open(root + "/" + name) as file:
                    return parse(await file.read())
            except asyncssh.SFTPNoSuchFile:
                return {} if parse is json.loads else None

        manifest = await read("manifest.json")
        journal = await read("update.json")
        worker = await read("state/worker-id", parse=lambda text: text.strip() or None)
        installed = bool(manifest) and "executable" in manifest
        return {
            "installed": installed,
            "path": root,
            "installed_version": manifest.get("version") if installed else None,
            "worker_id": worker,
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

    A broken sentineld install must still be repairable. None means the final
    check with the replacement sentineld must determine the affected workspaces.
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
    """One update-time request to the installed sentineld; never replays a mutation.

    status: {"states": {id: state}}. maintenance: stops the approved running workspaces, then
    shuts sentineld down; its EOF means it released the store for the update.
    """
    reader, writer, call = await _control(conn, root)
    try:
        listed = await asyncio.wait_for(call("list"), 5)
        records = listed.get("workspaces") or {}
        states = {id: record.get("state") for id, record in records.items()}
        if action == "status":
            return {"states": states}
        if action != "maintenance":
            raise RemoteMacError("Unknown runtime update action")
        running = [
            id
            for id, record in records.items()
            if record.get("state") == "running" or record.get("operation")
        ]
        if not set(running).issubset(values.get("approved_workspaces") or []):
            raise RemoteMacError("Running workspaces changed. Review and approve the update again.")
        async with asyncio.timeout(120 + 60 * len(running)):
            for id in running:
                await call("stop", workspace=id)
            await call("shutdown")
            while await reader.read(65536):
                pass
        return {"states": {id: "stopped" for id in records}}
    finally:
        writer.close()


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
        root = home + "/" + RUNTIME_DIRECTORY
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
        version = runtime_version([name for _, name in files], hashes)

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
        directories = sorted({str(PurePosixPath(release, name).parent) for _, name in files})
        await conn.run("umask 077; mkdir -p " + " ".join(map(quote, directories)), check=True)
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
                # The guest share (scripts, the guest agent) keeps its executable bits;
                # the release directory itself is private to this account.
                executable = os.stat(source).st_mode & 0o111
                await sftp.chmod(remote + ".partial", 0o755 if executable else 0o644)
                await sftp.posix_rename(remote + ".partial", remote)
        progress("Verifying runtime signature")
        for name in SIGNED:
            await conn.run(
                f"/usr/bin/codesign --verify --strict {quote(release + '/' + name)}", check=True
            )
        manifest = {
            "executable": release + _EXECUTABLE,
            # Read only by releases from before the VM process merged into sentineld: their
            # update coordinator requires it and passes it to the new release as --workspace,
            # which current releases accept and ignore. The directory does not exist.
            "workspace": release + "/sentinel-workspace",
            "files": [name for _, name in files],
            "version": version,
            "updateProtocol": 1,
            "storeVersion": 1,
        }
        progress("Acquiring remote update lock")
        async with remote_update.UpdateSession(conn, manifest["executable"], root) as session:
            from app.services.workspaces.machines.remote import migrations

            manifest["runtimeMigrations"], inputs = await migrations.prepare(session, machine.id)
            runtime = RemoteMacRuntime(
                dataclasses.replace(machine, host_key=host_key, runtime_root=root)
            )
            runtime.maintenance_owner = asyncio.current_task()
            # Talks to the running sentineld's socket, whichever release it is.
            transport = manifest

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
                await wait_for_runtime_ready(conn, root)
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
            try:
                conn = await self.ssh._ensure_conn()
            except (OSError, TimeoutError, asyncio.TimeoutError, asyncssh.Error) as exc:
                raise unreachable(self.machine, exc) from exc
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
                "--state",
                root + "/state",
                "--cache",
                root + "/cache",
            ]
            # sentineld's store lock rejects duplicate owners. No boot/login service.
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
            await wait_for_runtime_ready(conn, root)
            old_directory = self.directory
            self.directory = tempfile.mkdtemp(prefix="sentinel-remote-", dir="/tmp")
            os.chmod(self.directory, 0o700)
            socket = self.directory + "/control.sock"
            self.listener = await conn.forward_local_path(socket, _socket(root))
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
            RuntimeCompatibilityError.check(result, machine_id=self.machine.id)
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
