from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx

from app.config import settings
from app.services.runtime.base import (
    RuntimeCommandClient,
    RuntimeExecResult,
    RuntimeInstance,
    RuntimeProviderInfo,
    RuntimeProviderInfoItem,
)
from app.services.runtime.ssh_client import SSHClient

logger = logging.getLogger(__name__)

_DEFAULT_SESSION_ROOT = "/srv/sentinel/sessions"
_DEFAULT_WORKSPACE_ROOT_ENV = "SESSION_RUNTIME_BASE_DIR"


class QemuBridgeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class QemuProfile:
    image: str
    ssh_key_path: str
    cpus: int
    memory_mb: int
    run_root: str
    workspace_root: str
    ssh_port: int
    vnc_port: int
    cdp_port: int
    host: str
    public_host: str
    share_tag: str
    share_mount: str


def build_qemu_profile() -> QemuProfile:
    image = (settings.runtime_qemu_image or "").strip()
    key_path = (settings.runtime_qemu_ssh_key_path or "").strip()
    if not image:
        raise ValueError("RUNTIME_QEMU_IMAGE is required for the 'qemu' backend")
    if not key_path:
        raise ValueError("RUNTIME_QEMU_SSH_KEY_PATH is required for the 'qemu' backend")
    workspace_root = (settings.runtime_qemu_workspace_root or settings.runtime_workspaces_host_dir).strip()
    if not workspace_root:
        raise ValueError("RUNTIME_QEMU_WORKSPACE_ROOT or RUNTIME_WORKSPACES_HOST_DIR is required")
    return QemuProfile(
        image=image,
        ssh_key_path=key_path,
        cpus=max(1, int(settings.runtime_qemu_cpus)),
        memory_mb=max(1024, int(settings.runtime_qemu_memory_mb)),
        run_root=(settings.runtime_qemu_run_root or "/data/runtime/qemu").strip(),
        workspace_root=workspace_root,
        ssh_port=int(settings.runtime_qemu_ssh_port),
        vnc_port=int(settings.runtime_qemu_vnc_port),
        cdp_port=int(settings.runtime_qemu_cdp_port),
        host=(settings.runtime_qemu_host or "host.docker.internal").strip(),
        public_host=(settings.runtime_qemu_public_host or "localhost").strip(),
        share_tag=(settings.runtime_qemu_share_tag or "sentinel-host-workspaces").strip(),
        share_mount=(settings.runtime_qemu_share_mount or "/mnt/sentinel-host-workspaces").strip(),
    )


def _quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _session_guest_workspace(session_id: str) -> str:
    return f"{_DEFAULT_SESSION_ROOT}/{session_id}/workspace"


def _session_guest_profile(session_id: str) -> str:
    return f"{_DEFAULT_SESSION_ROOT}/{session_id}/browser-profile"


def _session_guest_runtime_dir(session_id: str) -> str:
    return f"{_DEFAULT_SESSION_ROOT}/{session_id}/runtime"


def _session_guest_venv_root(session_id: str) -> str:
    return f"{_DEFAULT_SESSION_ROOT}/{session_id}/venvs"


def _session_host_workspace(profile: QemuProfile, session_id: str) -> str:
    return os.path.join(profile.workspace_root, session_id, "workspace")


def _session_share_source(profile: QemuProfile, session_id: str) -> str:
    return f"{profile.share_mount.rstrip('/')}/{session_id}/workspace"


class QemuSessionClient:
    def __init__(
        self,
        *,
        ssh: SSHClient,
        session_user: str,
        workspace_path: str,
    ) -> None:
        self._ssh = ssh
        self._session_user = session_user
        self._workspace_path = workspace_path

    async def wait_ready(self, *, timeout: int = 60) -> None:
        _ = timeout
        return None

    async def run(
        self,
        command: str,
        *,
        timeout: int = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> RuntimeExecResult:
        if as_root:
            return await self._ssh.run(
                command,
                timeout=timeout,
                cwd=cwd or self._workspace_path,
                env=env,
                as_root=True,
            )

        wrapped = f"sudo -u {self._session_user} bash -lc {_quote(self._build_session_script(command, cwd=cwd, env=env))}"
        return await self._ssh.run(wrapped, timeout=timeout)

    async def run_detached(
        self,
        command: str,
        *,
        stdout_path: str,
        stderr_path: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> int:
        if as_root:
            return await self._ssh.run_detached(
                command,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                cwd=cwd or self._workspace_path,
                env=env,
                as_root=True,
            )

        return await self._ssh.run_detached_script(
            self._build_session_script(command, cwd=cwd, env=env),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            shell_prefix=f"sudo -u {self._session_user} bash -lc",
        )

    async def close(self) -> None:
        return None

    def _build_session_script(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        prefix: list[str] = []
        if env:
            for key, value in env.items():
                prefix.append(f"export {key}={_quote(value)};")
        target_cwd = cwd or self._workspace_path
        prefix.append(f"cd {_quote(target_cwd)} &&")
        prefix.append(command)
        return " ".join(prefix)


class QemuRuntimeProvider:
    def __init__(self) -> None:
        self._instances: dict[str, RuntimeInstance] = {}
        self._ensure_locks: dict[str, asyncio.Lock] = {}
        self._visual_lock = asyncio.Lock()
        self._active_visual_session: str | None = None
        self._profile = build_qemu_profile()
        self._bridge_url = settings.runtime_qemu_bridge_url.rstrip("/")
        self._bridge_token = (settings.runtime_qemu_bridge_token or "").strip()
        self._ssh: SSHClient | None = None

    @property
    def profile(self) -> QemuProfile:
        return self._profile

    async def bridge_health(self) -> dict[str, object]:
        headers = {"X-Sentinel-Bridge-Token": self._bridge_token}
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                response = await client.get(f"{self._bridge_url}/healthz", headers=headers)
            except Exception as exc:  # noqa: BLE001
                raise QemuBridgeError("QEMU bridge is not reachable") from exc
        if response.status_code != 200:
            raise QemuBridgeError(f"QEMU bridge health check failed ({response.status_code})")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise QemuBridgeError("QEMU bridge returned an invalid health response")
        return payload

    async def _bridge_post(self, path: str, payload: dict, *, timeout_seconds: int = 120) -> dict[str, object]:
        headers = {"X-Sentinel-Bridge-Token": self._bridge_token}
        async with httpx.AsyncClient(timeout=timeout_seconds + 5) as client:
            response = await client.post(
                f"{self._bridge_url}{path}",
                headers=headers,
                json=payload,
            )
        if response.status_code != 200:
            raise QemuBridgeError(f"QEMU bridge request failed for {path} ({response.status_code})")
        result = response.json()
        if not isinstance(result, dict):
            raise QemuBridgeError(f"QEMU bridge returned invalid payload for {path}")
        if result.get("ok") is not True:
            raise QemuBridgeError(str(result.get("error") or f"QEMU bridge request failed for {path}"))
        return result

    async def _bridge_ensure_dir(self, path: str) -> None:
        await self._bridge_post("/v1/ensure-dir", {"path": path}, timeout_seconds=20)

    async def _ensure_vm(self) -> None:
        await self._bridge_post(
            "/v1/qemu/ensure",
            {
                "run_root": self._profile.run_root,
                "image_path": self._profile.image,
                "ssh_port": self._profile.ssh_port,
                "vnc_port": self._profile.vnc_port,
                "cdp_port": self._profile.cdp_port,
                "cpus": self._profile.cpus,
                "memory_mb": self._profile.memory_mb,
                "workspace_root": self._profile.workspace_root,
                "share_tag": self._profile.share_tag,
            },
            timeout_seconds=60,
        )

    async def _stop_vm(self) -> None:
        await self._bridge_post("/v1/qemu/stop", {"run_root": self._profile.run_root}, timeout_seconds=20)

    async def _ensure_ssh(self) -> SSHClient:
        if self._ssh is None:
            self._ssh = SSHClient(
                host=self._profile.host,
                port=self._profile.ssh_port,
                username="builder",
                key_path=Path(self._profile.ssh_key_path),
            )
        await self._ssh.wait_ready(timeout=60)
        return self._ssh

    async def _run_root(self, command: str, *, timeout: int = 120) -> RuntimeExecResult:
        ssh = await self._ensure_ssh()
        return await ssh.run(f"sudo bash -lc {_quote(command)}", timeout=timeout)

    async def _ensure_workspace_share_mount(self) -> None:
        cmd = (
            f"mkdir -p {_quote(self._profile.share_mount)} && "
            f"mountpoint -q {_quote(self._profile.share_mount)} || "
            f"mount -t 9p -o trans=virtio,version=9p2000.L,msize=104857600 "
            f"{_quote(self._profile.share_tag)} {_quote(self._profile.share_mount)}"
        )
        result = await self._run_root(cmd, timeout=60)
        if result.exit_status != 0:
            raise QemuBridgeError(result.stderr or result.stdout or "Failed to mount QEMU workspace share")

    async def _prepare_session(self, session_id: str) -> dict[str, str]:
        host_workspace = _session_host_workspace(self._profile, session_id)
        await self._bridge_ensure_dir(host_workspace)
        await self._ensure_workspace_share_mount()
        guest_source = _session_share_source(self._profile, session_id)
        result = await self._run_root(
            f"/usr/local/bin/sentinel-session-prepare.sh "
            f"--session-id {_quote(session_id)} "
            f"--workspace-source {_quote(guest_source)}",
            timeout=60,
        )
        if result.exit_status != 0:
            raise QemuBridgeError(result.stderr or result.stdout or "Failed to prepare QEMU session")
        parsed: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            parsed[key.strip()] = value.strip()
        if not parsed.get("SESSION_USER") or not parsed.get("SESSION_WORKSPACE"):
            raise QemuBridgeError("QEMU session prepare output was incomplete")
        parsed["HOST_WORKSPACE"] = host_workspace
        return parsed

    async def ensure(self, session_id: UUID | str) -> RuntimeInstance:
        key = str(session_id)
        lock = self._ensure_locks.setdefault(key, asyncio.Lock())
        async with lock:
            existing = self._instances.get(key)
            if existing is not None:
                return existing

            await self.bridge_health()
            await self._ensure_vm()
            ssh = await self._ensure_ssh()
            await self._ensure_workspace_share_mount()
            session_env = await self._prepare_session(key)

            runtime = RuntimeInstance(
                session_id=key,
                client=QemuSessionClient(
                    ssh=ssh,
                    session_user=session_env["SESSION_USER"],
                    workspace_path=session_env["SESSION_WORKSPACE"],
                ),
                workspace_path=session_env["SESSION_WORKSPACE"],
                host=self._profile.host,
                metadata={
                    "provider": "qemu",
                    "session_user": session_env["SESSION_USER"],
                    "session_root": session_env.get("SESSION_ROOT"),
                    "session_home": session_env.get("SESSION_HOME"),
                    "session_profile": session_env.get("SESSION_PROFILE"),
                    "session_runtime_dir": session_env.get("SESSION_RUNTIME_DIR"),
                    "python_venv_root": _session_guest_venv_root(key),
                    "host_workspace": session_env.get("HOST_WORKSPACE"),
                },
            )
            self._instances[key] = runtime
            return runtime

    async def activate_session(self, session_id: UUID | str) -> RuntimeInstance:
        runtime = await self.ensure(session_id)
        await self._activate_visual_session(str(session_id), runtime)
        return runtime

    async def describe(self, session_id: UUID | str) -> RuntimeProviderInfo:
        key = str(session_id)
        runtime = self._instances.get(key)
        status = "unknown"
        pid = "—"
        try:
            bridge_status = await self._bridge_post("/v1/qemu/status", {"run_root": self._profile.run_root}, timeout_seconds=10)
            running = bool(bridge_status.get("running"))
            status = "running" if running else "stopped"
            if bridge_status.get("pid") is not None:
                pid = str(bridge_status.get("pid"))
        except Exception:
            logger.debug("Could not fetch QEMU VM status for session %s", key, exc_info=True)
        items = [
            RuntimeProviderInfoItem(key="vm_mode", label="VM Mode", value="Shared VM"),
            RuntimeProviderInfoItem(key="state", label="State", value=status.upper()),
            RuntimeProviderInfoItem(key="pid", label="VM PID", value=pid),
            RuntimeProviderInfoItem(key="host", label="Host", value=self._profile.public_host),
            RuntimeProviderInfoItem(key="ssh_port", label="SSH Port", value=str(self._profile.ssh_port)),
            RuntimeProviderInfoItem(key="vnc_port", label="VNC Port", value=str(self._profile.vnc_port)),
            RuntimeProviderInfoItem(key="cdp_port", label="CDP Port", value=str(self._profile.cdp_port)),
        ]
        if runtime is not None:
            session_user = str(runtime.metadata.get("session_user") or "")
            if session_user:
                items.append(RuntimeProviderInfoItem(key="session_user", label="Session User", value=session_user))
        summary = {
            "running": "Shared QEMU runtime VM is running.",
            "stopped": "Shared QEMU runtime VM is stopped.",
            "unknown": "QEMU runtime VM status is unavailable.",
        }.get(status, "QEMU runtime VM status is unavailable.")
        return RuntimeProviderInfo(
            id="qemu",
            label="QEMU",
            status=status,
            summary=summary,
            items=items,
        )

    async def hard_restart(self, session_id: UUID | str) -> RuntimeInstance:
        await self.stop_all()
        return await self.activate_session(session_id)

    async def _activate_visual_session(self, session_id: str, runtime: RuntimeInstance) -> None:
        async with self._visual_lock:
            if self._active_visual_session == session_id:
                return
            await self.restart_terminal(session_id, runtime)
            await self.restart_browser(session_id, runtime)
            self._active_visual_session = session_id

    async def destroy(self, session_id: UUID | str) -> None:
        key = str(session_id)
        runtime = self._instances.pop(key, None)
        if runtime is None:
            return
        if self._active_visual_session == key:
            self._active_visual_session = None
        result = await self._run_root(
            f"/usr/local/bin/sentinel-session-cleanup.sh --session-id {_quote(key)}",
            timeout=60,
        )
        if result.exit_status != 0:
            logger.warning("Failed to cleanup QEMU session %s: %s", key, result.stderr or result.stdout)

    async def stop(self, session_id: UUID | str) -> bool:
        key = str(session_id)
        runtime = self._instances.get(key)
        if runtime is None:
            return False
        await self.destroy(key)
        return True

    async def stop_all(self) -> int:
        keys = list(self._instances.keys())
        for key in keys:
            await self.destroy(key)
        await self._stop_vm()
        self._active_visual_session = None
        if self._ssh is not None:
            await self._ssh.close()
            self._ssh = None
        return len(keys)

    def get(self, session_id: UUID | str) -> RuntimeInstance | None:
        return self._instances.get(str(session_id))

    async def recover_existing(self) -> int:
        return 0

    def get_host(self, session_id: UUID | str) -> str | None:
        _ = session_id
        return self._profile.host

    def get_public_host(self, session_id: UUID | str) -> str | None:
        _ = session_id
        host = (settings.runtime_forward_public_host or "").strip()
        return host or self._profile.public_host

    def resolve_port(self, session_id: UUID | str, internal_port: int) -> int | None:
        _ = session_id
        port_map = {
            22: self._profile.ssh_port,
            6080: self._profile.vnc_port,
            9223: self._profile.cdp_port,
        }
        return port_map.get(int(internal_port))

    async def restart_terminal(self, session_id: UUID | str, runtime: RuntimeInstance) -> None:
        session_user = str(runtime.metadata.get("session_user") or "sentinel")
        workspace = runtime.workspace_path or _session_guest_workspace(str(session_id))
        runtime_dir = str(runtime.metadata.get("session_runtime_dir") or _session_guest_runtime_dir(str(session_id)))
        title = f"Sentinel Session {str(session_id)[:8]}"
        terminal_log = "/tmp/sentinel-session-terminal.log"
        cmd = (
            "pkill -x konsole >/dev/null 2>&1 || true; "
            "pkill -x xterm >/dev/null 2>&1 || true; "
            f"mkdir -p {_quote(runtime_dir)} {_quote(workspace)} && "
            f"chown {session_user}:{session_user} {_quote(runtime_dir)} >/dev/null 2>&1 || true; "
            f"chmod 700 {_quote(runtime_dir)} >/dev/null 2>&1 || true; "
            "if command -v konsole >/dev/null 2>&1; then "
            f"runuser -u {session_user} -- env DISPLAY=:99 XDG_RUNTIME_DIR={_quote(runtime_dir)} "
            f"nohup konsole --workdir {_quote(workspace)} --title {_quote(title)} --hold "
            f"-e /bin/bash -lc {_quote(f'cd {workspace} && printf \"Session: {session_id}\\\\nWorkspace: {workspace}\\\\n\" && exec bash')} "
            f">{_quote(terminal_log)} 2>&1 & "
            "else "
            f"runuser -u {session_user} -- env DISPLAY=:99 XDG_RUNTIME_DIR={_quote(runtime_dir)} "
            f"nohup xterm -geometry 120x36+60+60 -fa Monospace -fs 11 -title {_quote(title)} "
            f"-e /bin/bash -lc {_quote(f'cd {workspace} && printf \"Session: {session_id}\\\\nWorkspace: {workspace}\\\\n\" && exec bash')} "
            f">{_quote(terminal_log)} 2>&1 & "
            "fi; "
            "for i in $(seq 1 20); do "
            f"if pgrep -u {session_user} -x konsole >/dev/null 2>&1 || pgrep -u {session_user} -x xterm >/dev/null 2>&1; then exit 0; fi; "
            "sleep 0.5; "
            "done; "
            f"cat {_quote(terminal_log)} 2>/dev/null || true; "
            "exit 1"
        )
        result = await self._run_root(cmd, timeout=30)
        if result.exit_status != 0:
            raise QemuBridgeError(result.stderr or result.stdout or "QEMU terminal did not become ready")

    async def restart_browser(self, session_id: UUID | str, runtime: RuntimeInstance) -> None:
        session_user = str(runtime.metadata.get("session_user") or "sentinel")
        profile = str(runtime.metadata.get("session_profile") or _session_guest_profile(str(session_id)))
        runtime_dir = str(runtime.metadata.get("session_runtime_dir") or _session_guest_runtime_dir(str(session_id)))
        cmd = (
            f"systemctl set-environment "
            f"SENTINEL_BROWSER_USER={_quote(session_user)} "
            f"SENTINEL_BROWSER_PROFILE={_quote(profile)} "
            f"SENTINEL_BROWSER_RUNTIME_DIR={_quote(runtime_dir)} && "
            "systemctl restart sentinel-runtime-browser.service && "
            "for i in $(seq 1 60); do "
            "curl -fsS http://127.0.0.1:9223/json/version >/dev/null 2>&1 && exit 0; "
            "sleep 1; "
            "done; "
            "systemctl status sentinel-runtime-browser.service --no-pager || true; "
            "exit 1"
        )
        result = await self._run_root(cmd, timeout=90)
        if result.exit_status != 0:
            raise QemuBridgeError(result.stderr or result.stdout or "QEMU browser did not become ready")
