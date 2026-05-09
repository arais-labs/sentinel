from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.services.runtime.base import RuntimeInstance, RuntimeProviderInfo, RuntimeProviderInfoItem
from app.services.runtime.ssh_client import SSHClient

logger = logging.getLogger(__name__)

_REMOTE_BROWSER_RESTART_CMD = (
    "pkill -u sentinel -x chromium || true; "
    "pkill -u sentinel -x chromium-real || true; "
    "rm -f /home/sentinel/.config/chromium/SingletonLock "
    "/home/sentinel/.config/chromium/SingletonSocket "
    "/home/sentinel/.config/chromium/SingletonCookie 2>/dev/null || true; "
    "if ! pgrep -f 'socat TCP-LISTEN:9223' >/dev/null; then "
    "nohup socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222 "
    ">/tmp/chromium-socat.log 2>&1 & "
    "fi; "
    "nohup su - sentinel -c "
    "\"DISPLAY=:99 MESA_LOADER_DRIVER_OVERRIDE=llvmpipe chromium "
    "--disable-dev-shm-usage "
    "--use-gl=angle "
    "--use-angle=gl "
    "--ignore-gpu-blocklist "
    "--disable-gpu-driver-bug-workaround "
    "--remote-debugging-address=0.0.0.0 "
    "--remote-debugging-port=9222 "
    "--no-first-run "
    "--no-default-browser-check "
    "--window-size=1920,1080 "
    "about:blank\" "
    ">/tmp/chromium-reset.log 2>&1 &"
)
_REMOTE_BROWSER_READY_CHECK_CMD = (
    "for i in $(seq 1 30); do "
    "if curl -fsS http://127.0.0.1:9223/json/version >/dev/null 2>&1; then exit 0; fi; "
    "sleep 0.5; "
    "done; "
    "exit 1"
)


class RemoteRuntimeProvider:
    """Manages remote SSH-backed runtimes, one per session."""

    def __init__(self) -> None:
        if not settings.runtime_ssh_host:
            raise ValueError("RUNTIME_SSH_HOST is required for the 'remote' backend")
        self._host = settings.runtime_ssh_host
        self._port = settings.runtime_ssh_port
        self._user = settings.runtime_ssh_user
        self._key_path = Path(settings.runtime_ssh_key_path) if settings.runtime_ssh_key_path else None
        self._base_workspace = settings.runtime_ssh_workspace
        self._instances: dict[str, RuntimeInstance] = {}

    async def ensure(self, session_id: UUID | str) -> RuntimeInstance:
        key = str(session_id)
        if key in self._instances:
            return self._instances[key]

        workspace = f"{self._base_workspace}/{key}"

        ssh = SSHClient(
            host=self._host,
            port=self._port,
            username=self._user,
            key_path=self._key_path,
        )
        await ssh.wait_ready(timeout=30)
        await ssh.run(f"mkdir -p {workspace}", timeout=10)

        instance = RuntimeInstance(
            session_id=key,
            client=ssh,
            workspace_path=workspace,
            host=self._host,
        )
        self._instances[key] = instance
        logger.info("SSH runtime ready for session %s at %s:%d", key, self._host, self._port)
        return instance

    async def activate_session(self, session_id: UUID | str) -> RuntimeInstance:
        return await self.ensure(session_id)

    async def describe(self, session_id: UUID | str) -> RuntimeProviderInfo:
        key = str(session_id)
        runtime = self._instances.get(key)
        status = "connected" if runtime is not None else "idle"
        items = [
            RuntimeProviderInfoItem(key="host", label="Host", value=self._host),
            RuntimeProviderInfoItem(key="port", label="SSH Port", value=str(self._port)),
            RuntimeProviderInfoItem(key="user", label="User", value=self._user),
            RuntimeProviderInfoItem(
                key="workspace",
                label="Workspace",
                value=(runtime.workspace_path if runtime is not None else f"{self._base_workspace}/{key}"),
            ),
        ]
        summary = (
            "Remote SSH runtime is connected for this session."
            if runtime is not None
            else "Remote SSH runtime is not connected for this session."
        )
        return RuntimeProviderInfo(
            id="remote",
            label="SSH",
            status=status,
            summary=summary,
            items=items,
        )

    async def hard_restart(self, session_id: UUID | str) -> RuntimeInstance:
        await self.stop(session_id)
        return await self.activate_session(session_id)

    async def destroy(self, session_id: UUID | str) -> None:
        await self.stop(session_id)

    async def stop(self, session_id: UUID | str) -> bool:
        key = str(session_id)
        instance = self._instances.pop(key, None)
        if instance is None:
            return False
        await instance.client.close()
        logger.info("SSH runtime closed for session %s", key)
        return True

    async def stop_all(self) -> int:
        count = len(self._instances)
        for inst in self._instances.values():
            try:
                await inst.client.close()
            except Exception:
                logger.debug("SSH close error for %s", inst.session_id, exc_info=True)
        self._instances.clear()
        return count

    async def recover_existing(self) -> int:
        return 0

    def get(self, session_id: UUID | str) -> RuntimeInstance | None:
        return self._instances.get(str(session_id))

    def get_host(self, session_id: UUID | str) -> str | None:
        instance = self.get(session_id)
        return instance.host if instance is not None else None

    def get_public_host(self, session_id: UUID | str) -> str | None:
        _ = session_id
        return self._host

    def resolve_port(self, session_id: UUID | str, internal_port: int) -> int | None:
        _ = session_id
        return int(internal_port)

    async def restart_browser(self, session_id: UUID | str, runtime: RuntimeInstance) -> None:
        _ = session_id
        await runtime.client.run(_REMOTE_BROWSER_RESTART_CMD, timeout=15)
        result = await runtime.client.run(_REMOTE_BROWSER_READY_CHECK_CMD, timeout=20)
        if result.exit_status != 0:
            raise RuntimeError("Browser CDP did not become ready")


_provider: RemoteRuntimeProvider | None = None


def get_remote_runtime() -> RemoteRuntimeProvider:
    global _provider
    if _provider is None:
        _provider = RemoteRuntimeProvider()
    return _provider
