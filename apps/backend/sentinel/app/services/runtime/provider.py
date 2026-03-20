from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.services.runtime.ssh_client import SSHClient

logger = logging.getLogger(__name__)


@dataclass
class RuntimeInstance:
    session_id: str
    ssh: SSHClient
    workspace_path: str


class SSHRuntimeProvider:
    """Manages SSH connections to a remote machine, one per session."""

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

        instance = RuntimeInstance(session_id=key, ssh=ssh, workspace_path=workspace)
        self._instances[key] = instance
        logger.info("SSH runtime ready for session %s at %s:%d", key, self._host, self._port)
        return instance

    async def stop(self, session_id: UUID | str) -> bool:
        key = str(session_id)
        instance = self._instances.pop(key, None)
        if instance is None:
            return False
        await instance.ssh.close()
        logger.info("SSH runtime closed for session %s", key)
        return True

    async def stop_all(self) -> int:
        count = len(self._instances)
        for inst in self._instances.values():
            try:
                await inst.ssh.close()
            except Exception:
                logger.debug("SSH close error for %s", inst.session_id, exc_info=True)
        self._instances.clear()
        return count

    def get(self, session_id: UUID | str) -> RuntimeInstance | None:
        return self._instances.get(str(session_id))


_provider: SSHRuntimeProvider | None = None


def get_ssh_runtime() -> SSHRuntimeProvider:
    global _provider
    if _provider is None:
        _provider = SSHRuntimeProvider()
    return _provider
