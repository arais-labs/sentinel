from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.services.runtime.base import (
    RuntimeInstance,
    RuntimeProviderInfo,
    RuntimeProviderInfoItem,
)
from app.services.runtime.qemu.controls import BridgeQemuControl, DesktopQemuControl, QemuBridgeError, QemuControl
from app.services.runtime.qemu.profile import QemuProfile, build_qemu_profile
from app.services.runtime.qemu.session import (
    QemuSessionClient,
    quote,
    session_guest_profile,
    session_guest_runtime_dir,
    session_guest_venv_root,
    session_guest_workspace,
    session_host_workspace,
    session_share_source,
)
from app.services.runtime.ssh_client import SSHClient

logger = logging.getLogger(__name__)


def qemu_control_mode() -> str:
    configured = (settings.runtime_qemu_control or "").strip().lower()
    if configured:
        return configured
    return "desktop" if settings.app_env == "desktop" else "bridge"


def build_qemu_control() -> QemuControl:
    mode = qemu_control_mode()
    if mode == "desktop":
        return DesktopQemuControl()
    if mode == "bridge":
        return BridgeQemuControl()
    raise ValueError("RUNTIME_QEMU_CONTROL must be 'bridge' or 'desktop'")


class QemuRuntimeProvider:
    def __init__(self) -> None:
        self._instances: dict[str, RuntimeInstance] = {}
        self._ensure_locks: dict[str, asyncio.Lock] = {}
        self._base_image_lock = asyncio.Lock()
        self._base_image_task: asyncio.Task[None] | None = None
        self._base_image_status: dict[str, object] = {
            "state": "unknown",
            "message": "QEMU base image status has not been checked yet.",
        }
        self._visual_lock = asyncio.Lock()
        self._active_visual_session: str | None = None
        self._profile = build_qemu_profile()
        self._control = build_qemu_control()
        self._ssh: SSHClient | None = None

    @property
    def profile(self) -> QemuProfile:
        return self._profile

    async def bridge_health(self) -> dict[str, object]:
        return await self._control.health()

    def start_background_prepare(self) -> None:
        if qemu_control_mode() != "desktop":
            return
        if self._base_image_task is not None and not self._base_image_task.done():
            return
        self._base_image_task = asyncio.create_task(self._background_prepare_base_image())

    async def cancel_background_prepare(self) -> None:
        task = self._base_image_task
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _background_prepare_base_image(self) -> None:
        try:
            await self.bridge_health()
            await self._ensure_base_image()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._base_image_status = {
                "state": "failed",
                "message": str(exc),
                "image_path": self._profile.image,
                "key_path": self._profile.ssh_key_path,
            }
            logger.warning("QEMU base image background preparation failed: %s", exc)

    async def _ensure_base_image(self) -> None:
        async with self._base_image_lock:
            status = await self._control.base_image_status(self._profile)
            if status.get("state") == "ready":
                self._base_image_status = status
                return
            self._base_image_status = {
                **status,
                "state": "building",
                "message": "QEMU base image is being prepared.",
            }
            try:
                await self._control.ensure_base_image(self._profile)
                self._base_image_status = await self._control.base_image_status(self._profile)
            except Exception as exc:
                self._base_image_status = {
                    **status,
                    "state": "failed",
                    "message": str(exc),
                }
                raise

    async def _ensure_vm(self) -> None:
        await self._control.ensure_vm(self._profile)

    async def _stop_vm(self) -> None:
        await self._control.stop_vm(self._profile)

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

    async def _run_root(self, command: str, *, timeout: int = 120):
        ssh = await self._ensure_ssh()
        return await ssh.run(f"sudo bash -lc {quote(command)}", timeout=timeout)

    async def _ensure_workspace_share_mount(self) -> None:
        cmd = (
            f"mkdir -p {quote(self._profile.share_mount)} && "
            f"mountpoint -q {quote(self._profile.share_mount)} || "
            f"mount -t 9p -o trans=virtio,version=9p2000.L,msize=104857600 "
            f"{quote(self._profile.share_tag)} {quote(self._profile.share_mount)}"
        )
        result = await self._run_root(cmd, timeout=60)
        if result.exit_status != 0:
            raise QemuBridgeError(result.stderr or result.stdout or "Failed to mount QEMU workspace share")

    async def _prepare_session(self, session_id: str) -> dict[str, str]:
        host_workspace = session_host_workspace(self._profile, session_id)
        await self._control.ensure_dir(host_workspace)
        await self._ensure_workspace_share_mount()
        guest_source = session_share_source(self._profile, session_id)
        result = await self._run_root(
            f"/usr/local/bin/sentinel-session-prepare.sh "
            f"--session-id {quote(session_id)} "
            f"--workspace-source {quote(guest_source)}",
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
            await self._ensure_base_image()
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
                    "python_venv_root": session_guest_venv_root(key),
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
        base_status = self._base_image_status
        if base_status.get("state") == "unknown":
            try:
                base_status = await self._control.base_image_status(self._profile)
                self._base_image_status = base_status
            except Exception as exc:
                base_status = {"state": "failed", "message": str(exc)}
        try:
            qemu_status = await self._control.status(self._profile)
            running = bool(qemu_status.get("running"))
            status = "running" if running else "stopped"
            if qemu_status.get("pid") is not None:
                pid = str(qemu_status.get("pid"))
        except Exception:
            logger.debug("Could not fetch QEMU VM status for session %s", key, exc_info=True)
        base_state = str(base_status.get("state") or "unknown")
        if base_state in {"missing", "building", "invalid"}:
            status = "starting"
        elif base_state == "failed":
            status = "failed"
        items = [
            RuntimeProviderInfoItem(key="vm_mode", label="VM Mode", value="Shared VM"),
            RuntimeProviderInfoItem(key="base_image", label="Base Image", value=base_state.upper()),
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
            "starting": str(base_status.get("message") or "QEMU runtime image is being prepared."),
            "failed": str(base_status.get("message") or "QEMU runtime image preparation failed."),
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
            f"/usr/local/bin/sentinel-session-cleanup.sh --session-id {quote(key)}",
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
        await self.cancel_background_prepare()
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
        workspace = runtime.workspace_path or session_guest_workspace(str(session_id))
        runtime_dir = str(runtime.metadata.get("session_runtime_dir") or session_guest_runtime_dir(str(session_id)))
        title = f"Sentinel Session {str(session_id)[:8]}"
        terminal_log = "/tmp/sentinel-session-terminal.log"
        cmd = (
            "pkill -x konsole >/dev/null 2>&1 || true; "
            "pkill -x xterm >/dev/null 2>&1 || true; "
            f"mkdir -p {quote(runtime_dir)} {quote(workspace)} && "
            f"chown {session_user}:{session_user} {quote(runtime_dir)} >/dev/null 2>&1 || true; "
            f"chmod 700 {quote(runtime_dir)} >/dev/null 2>&1 || true; "
            "if command -v konsole >/dev/null 2>&1; then "
            f"runuser -u {session_user} -- env DISPLAY=:99 XDG_RUNTIME_DIR={quote(runtime_dir)} "
            f"nohup konsole --workdir {quote(workspace)} --title {quote(title)} --hold "
            f"-e /bin/bash -lc {quote(f'cd {workspace} && printf \"Session: {session_id}\\\\nWorkspace: {workspace}\\\\n\" && exec bash')} "
            f">{quote(terminal_log)} 2>&1 & "
            "else "
            f"runuser -u {session_user} -- env DISPLAY=:99 XDG_RUNTIME_DIR={quote(runtime_dir)} "
            f"nohup xterm -geometry 120x36+60+60 -fa Monospace -fs 11 -title {quote(title)} "
            f"-e /bin/bash -lc {quote(f'cd {workspace} && printf \"Session: {session_id}\\\\nWorkspace: {workspace}\\\\n\" && exec bash')} "
            f">{quote(terminal_log)} 2>&1 & "
            "fi; "
            "for i in $(seq 1 20); do "
            f"if pgrep -u {session_user} -x konsole >/dev/null 2>&1 || pgrep -u {session_user} -x xterm >/dev/null 2>&1; then exit 0; fi; "
            "sleep 0.5; "
            "done; "
            f"cat {quote(terminal_log)} 2>/dev/null || true; "
            "exit 1"
        )
        result = await self._run_root(cmd, timeout=30)
        if result.exit_status != 0:
            raise QemuBridgeError(result.stderr or result.stdout or "QEMU terminal did not become ready")

    async def restart_browser(self, session_id: UUID | str, runtime: RuntimeInstance) -> None:
        session_user = str(runtime.metadata.get("session_user") or "sentinel")
        profile = str(runtime.metadata.get("session_profile") or session_guest_profile(str(session_id)))
        runtime_dir = str(runtime.metadata.get("session_runtime_dir") or session_guest_runtime_dir(str(session_id)))
        cmd = (
            f"systemctl set-environment "
            f"SENTINEL_BROWSER_USER={quote(session_user)} "
            f"SENTINEL_BROWSER_PROFILE={quote(profile)} "
            f"SENTINEL_BROWSER_RUNTIME_DIR={quote(runtime_dir)} && "
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
