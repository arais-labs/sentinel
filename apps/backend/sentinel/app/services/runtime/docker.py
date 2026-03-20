from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.services.runtime.provider import RuntimeInstance
from app.services.runtime.ssh_client import SSHClient

logger = logging.getLogger(__name__)

CONTAINER_WORKSPACE = "/home/sentinel/workspace"
_NAME_PREFIX = "sentinel-runtime-"


class DockerRuntimeProvider:
    """Launches a Docker container per session and SSHs into it."""

    def __init__(self) -> None:
        self._image = settings.runtime_image
        self._network = settings.runtime_docker_network
        self._memory = settings.runtime_memory_limit
        self._cpus = settings.runtime_cpu_limit
        self._instances: dict[str, _DockerInstance] = {}
        self._ssh_key_dir = Path(settings.runtime_ssh_key_dir)
        self._ssh_key_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_ssh_keypair()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ensure(self, session_id: UUID | str) -> RuntimeInstance:
        key = str(session_id)
        if key in self._instances:
            return self._instances[key].runtime

        container_name = f"{_NAME_PREFIX}{key[:12]}"

        # Check if a container with this name is already running (e.g. after
        # a hot-reload or backend restart).
        existing = await self._inspect_running_container(container_name)
        if existing:
            container_id, ip = existing
            logger.info(
                "Reconnecting to existing runtime container %s (%s) for session %s",
                container_name, ip, key,
            )
        else:
            # Remove any stale stopped container with the same name
            await asyncio.to_thread(
                subprocess.run,
                ["docker", "rm", "-f", container_name],
                capture_output=True,
            )

            pub_key = (self._ssh_key_dir / "id_ed25519.pub").read_text().strip()

            # Create per-session workspace directory on the host so it persists
            # across container restarts. The path is bind-mounted into the container.
            host_ws = Path(settings.runtime_workspaces_host_dir) / key / "workspace"
            host_ws.mkdir(parents=True, exist_ok=True)

            # Launch container
            cmd = [
                "docker", "run", "-d",
                "--name", container_name,
                "--network", self._network,
                "--memory", self._memory,
                "--cpus", str(self._cpus),
                "-e", f"SSH_PUBLIC_KEY={pub_key}",
                "-v", f"{host_ws}:/home/sentinel/workspace",
                self._image,
            ]
            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, check=True,
            )
            container_id = result.stdout.strip()
            logger.info("Launched runtime container %s for session %s", container_name, key)

            # Get container IP on the shared network
            ip = await self._get_container_ip(container_id)

        # SSH into it
        ssh = SSHClient(
            host=ip,
            port=22,
            username="sentinel",
            key_path=self._ssh_key_dir / "id_ed25519",
        )
        await ssh.wait_ready(timeout=60)

        runtime = RuntimeInstance(
            session_id=key,
            ssh=ssh,
            workspace_path=CONTAINER_WORKSPACE,
        )
        self._instances[key] = _DockerInstance(
            container_id=container_id,
            container_name=container_name,
            ip=ip,
            runtime=runtime,
        )
        logger.info("Runtime ready for session %s at %s", key, ip)
        return runtime

    async def destroy(self, session_id) -> None:
        """Kill and remove the runtime container for a session."""
        key = str(session_id)
        inst = self._instances.pop(key, None)
        container_name = f"{_NAME_PREFIX}{key[:12]}"
        if inst is not None:
            try:
                await inst.runtime.ssh.close()
            except Exception:
                pass
            container_name = inst.container_name
        await asyncio.to_thread(
            subprocess.run,
            ["docker", "rm", "-f", container_name],
            capture_output=True,
        )
        logger.info("Destroyed runtime container %s for session %s", container_name, key)

    async def recover_existing(self) -> int:
        """Discover running sentinel-runtime containers and register them.

        Call this on startup so VNC proxy / live-view work for containers
        that survived a backend restart or hot-reload.
        """
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "docker", "ps",
                "--filter", f"name={_NAME_PREFIX}",
                "--format", "{{json .}}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return 0

        recovered = 0
        for line in result.stdout.strip().splitlines():
            try:
                info = json.loads(line)
            except json.JSONDecodeError:
                continue

            name: str = info.get("Names", "")
            container_id: str = info.get("ID", "")
            if not name.startswith(_NAME_PREFIX) or not container_id:
                continue

            # Derive session_id prefix from container name
            session_prefix = name[len(_NAME_PREFIX):]

            try:
                ip = await self._get_container_ip(container_id)
            except Exception:
                logger.warning("Could not get IP for existing container %s", name)
                continue

            # Check if we already track this
            already_tracked = any(
                inst.container_id == container_id for inst in self._instances.values()
            )
            if already_tracked:
                continue

            # Try to SSH into it
            try:
                ssh = SSHClient(
                    host=ip,
                    port=22,
                    username="sentinel",
                    key_path=self._ssh_key_dir / "id_ed25519",
                )
                await ssh.wait_ready(timeout=10)
            except Exception:
                logger.warning("Could not SSH into existing container %s at %s", name, ip)
                continue

            # We need the full session_id. Look it up from existing sessions
            # that match the prefix. For now, store with the prefix as key
            # and also try to find the full UUID.
            full_key = await self._resolve_full_session_id(session_prefix)
            if not full_key:
                full_key = session_prefix  # fallback

            runtime = RuntimeInstance(
                session_id=full_key,
                ssh=ssh,
                workspace_path=CONTAINER_WORKSPACE,
            )
            self._instances[full_key] = _DockerInstance(
                container_id=container_id,
                container_name=name,
                ip=ip,
                runtime=runtime,
            )
            recovered += 1
            logger.info("Recovered runtime container %s for session %s at %s", name, full_key, ip)

        return recovered

    async def stop(self, session_id: UUID | str) -> bool:
        key = str(session_id)
        inst = self._instances.pop(key, None)
        if inst is None:
            return False
        await inst.runtime.ssh.close()
        await asyncio.to_thread(
            subprocess.run,
            ["docker", "rm", "-f", inst.container_id],
            capture_output=True,
        )
        logger.info("Stopped runtime container %s for session %s", inst.container_name, key)
        return True

    async def stop_all(self) -> int:
        keys = list(self._instances.keys())
        for key in keys:
            await self.stop(key)
        return len(keys)

    def get(self, session_id: UUID | str) -> RuntimeInstance | None:
        inst = self._instances.get(str(session_id))
        return inst.runtime if inst else None

    def get_container_ip(self, session_id: UUID | str) -> str | None:
        key = str(session_id)
        inst = self._instances.get(key)
        if inst:
            return inst.ip
        # Fallback: check if a container exists on Docker (e.g. after hot-reload)
        return self._get_container_ip_sync(f"{_NAME_PREFIX}{key[:12]}")

    def _get_container_ip_sync(self, container_name: str) -> str | None:
        """Synchronous fallback to get container IP directly from Docker."""
        try:
            tmpl = '{{(index .NetworkSettings.Networks "' + self._network + '").IPAddress}}'
            result = subprocess.run(
                ["docker", "inspect", "-f", tmpl, container_name],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return None
            ip = result.stdout.strip()
            return ip if ip else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_ssh_keypair(self) -> None:
        key_path = self._ssh_key_dir / "id_ed25519"
        if key_path.exists():
            return
        logger.info("Generating runtime SSH keypair at %s", key_path)
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", "sentinel-runtime"],
            check=True,
            capture_output=True,
        )
        key_path.chmod(0o600)

    async def _get_container_ip(self, container_id: str) -> str:
        # Use `index` to handle network names with special characters (hyphens)
        tmpl = '{{(index .NetworkSettings.Networks "' + self._network + '").IPAddress}}'
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "inspect", "-f", tmpl, container_id],
            capture_output=True,
            text=True,
            check=True,
        )
        ip = result.stdout.strip()
        if not ip:
            raise RuntimeError(f"Could not get IP for container {container_id}")
        return ip

    async def _inspect_running_container(self, container_name: str) -> tuple[str, str] | None:
        """Return (container_id, ip) if a running container with this name exists."""
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "inspect", "--format", "{{.Id}} {{.State.Running}}", container_name],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split()
        if len(parts) < 2 or parts[1] != "true":
            return None
        container_id = parts[0]
        try:
            ip = await self._get_container_ip(container_id)
        except Exception:
            return None
        return container_id, ip

    async def _resolve_full_session_id(self, prefix: str) -> str | None:
        """Try to find the full UUID for a session prefix from the database."""
        try:
            from app.database import AsyncSessionLocal
            from sqlalchemy import text
            async with AsyncSessionLocal() as session:
                row = await session.execute(
                    text("SELECT id FROM sessions WHERE CAST(id AS TEXT) LIKE :prefix LIMIT 1"),
                    {"prefix": f"{prefix}%"},
                )
                result = row.fetchone()
                return str(result[0]) if result else None
        except Exception:
            logger.debug("Could not resolve session prefix %s from DB", prefix, exc_info=True)
            return None


class _DockerInstance:
    __slots__ = ("container_id", "container_name", "ip", "runtime")

    def __init__(self, *, container_id: str, container_name: str, ip: str, runtime: RuntimeInstance) -> None:
        self.container_id = container_id
        self.container_name = container_name
        self.ip = ip
        self.runtime = runtime
