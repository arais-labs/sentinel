from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.services.runtime.base import RuntimeInstance, RuntimeProviderInfo, RuntimeProviderInfoItem
from app.services.runtime.playwright_runtime import (
    DEFAULT_BROWSER_LOCALE,
    DEFAULT_BROWSER_TIMEZONE_ID,
)
from app.services.runtime.ssh_client import SSHClient

logger = logging.getLogger(__name__)

CONTAINER_WORKSPACE = "/home/sentinel/workspace"
_NAME_PREFIX = "sentinel-runtime-"
_DOCKER_BROWSER_RESTART_CMD = (
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
_DOCKER_BROWSER_READY_CHECK_CMD = (
    "for i in $(seq 1 30); do "
    "if curl -fsS http://127.0.0.1:9223/json/version >/dev/null 2>&1; then exit 0; fi; "
    "sleep 0.5; "
    "done; "
    "exit 1"
)


def _compose_project_from_network(network: str) -> str | None:
    value = (network or "").strip()
    if not value:
        return None
    if value.endswith("_default"):
        return value[: -len("_default")] or None
    return None


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
            await self._remove_container(container_name)

            pub_key = (self._ssh_key_dir / "id_ed25519.pub").read_text().strip()

            # Create through the backend-visible mount, but bind the host path
            # into the runtime container. In Docker Compose these are different
            # strings for the same directory.
            backend_ws = Path(settings.session_runtime_base_dir) / key / "workspace"
            host_ws = Path(settings.runtime_workspaces_host_dir) / key / "workspace"
            backend_ws.mkdir(parents=True, exist_ok=True)

            # Launch container
            compose_project = _compose_project_from_network(self._network)
            cmd = [
                "docker", "run", "-d",
                "--name", container_name,
                "--network", self._network,
                "-P",
                "--cap-add=SYS_ADMIN",
                "--security-opt", "seccomp=unconfined",
                "--memory", self._memory,
                "--cpus", str(self._cpus),
                "-e", f"SSH_PUBLIC_KEY={pub_key}",
                "-v", f"{host_ws}:/home/sentinel/workspace",
            ]
            browser_user_agent = os.getenv("BROWSER_USER_AGENT", "").strip()
            browser_locale = os.getenv("BROWSER_LOCALE", "").strip() or DEFAULT_BROWSER_LOCALE
            browser_timezone = os.getenv("BROWSER_TIMEZONE_ID", "").strip() or DEFAULT_BROWSER_TIMEZONE_ID
            if browser_user_agent:
                cmd.extend(["-e", f"BROWSER_USER_AGENT={browser_user_agent}"])
            cmd.extend(
                [
                    "-e",
                    f"BROWSER_LOCALE={browser_locale}",
                    "-e",
                    f"BROWSER_TIMEZONE_ID={browser_timezone}",
                    "-e",
                    f"TZ={browser_timezone}",
                ]
            )
            if compose_project:
                cmd.extend(
                    [
                        "--label", f"com.docker.compose.project={compose_project}",
                        "--label", "com.docker.compose.service=sentinel-runtime",
                    ]
                )
            cmd.append(self._image)
            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True,
            )
            if result.returncode != 0:
                await self._remove_container(container_name)
                detail = (result.stderr or result.stdout or "docker run failed without output").strip()
                logger.error(
                    "Docker runtime launch failed for %s with exit %s; image=%s network=%s workspace=%s stdout=%r stderr=%r",
                    container_name,
                    result.returncode,
                    self._image,
                    self._network,
                    host_ws,
                    (result.stdout or "").strip(),
                    (result.stderr or "").strip(),
                )
                raise RuntimeError(
                    f"Docker runtime launch failed for {container_name} "
                    f"(exit {result.returncode}): {detail}"
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
            client=ssh,
            workspace_path=CONTAINER_WORKSPACE,
            host=ip,
        )
        self._instances[key] = _DockerInstance(
            container_id=container_id,
            container_name=container_name,
            ip=ip,
            runtime=runtime,
        )
        logger.info("Runtime ready for session %s at %s", key, ip)
        return runtime

    async def activate_session(self, session_id: UUID | str) -> RuntimeInstance:
        return await self.ensure(session_id)

    async def describe(self, session_id: UUID | str) -> RuntimeProviderInfo:
        key = str(session_id)
        inst = self._instances.get(key)
        container_name = inst.container_name if inst is not None else f"{_NAME_PREFIX}{key[:12]}"
        status = "missing"
        container_id = inst.container_id if inst is not None else ""
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "inspect", "--format", "{{.Id}} {{.State.Status}}", container_name],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            parts = (result.stdout or "").strip().split()
            if len(parts) >= 2:
                container_id = parts[0]
                status = parts[1].strip().lower() or "unknown"
        vnc_port = self.resolve_port(key, settings.runtime_live_port)
        cdp_port = self.resolve_port(key, 9223)
        host = self.get_public_host(key) or "localhost"
        items = [
            RuntimeProviderInfoItem(key="container", label="Container", value=container_name),
            RuntimeProviderInfoItem(key="state", label="State", value=status.upper()),
            RuntimeProviderInfoItem(key="container_id", label="Container ID", value=(container_id[:12] if container_id else "—")),
            RuntimeProviderInfoItem(key="host", label="Host", value=host),
            RuntimeProviderInfoItem(key="vnc_port", label="VNC Port", value=(str(vnc_port) if vnc_port else "—")),
            RuntimeProviderInfoItem(key="cdp_port", label="CDP Port", value=(str(cdp_port) if cdp_port else "—")),
        ]
        summary = {
            "running": "Per-session container is running.",
            "created": "Container exists but is not running yet.",
            "exited": "Container exists but is stopped.",
            "missing": "Container has not been created yet.",
        }.get(status, f"Container state: {status}.")
        return RuntimeProviderInfo(
            id="docker",
            label="Docker",
            status=status,
            summary=summary,
            items=items,
        )

    async def hard_restart(self, session_id: UUID | str) -> RuntimeInstance:
        await self.destroy(session_id)
        return await self.activate_session(session_id)

    async def destroy(self, session_id) -> None:
        """Kill and remove the runtime container for a session."""
        key = str(session_id)
        inst = self._instances.pop(key, None)
        container_name = f"{_NAME_PREFIX}{key[:12]}"
        if inst is not None:
            try:
                await inst.runtime.client.close()
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
                client=ssh,
                workspace_path=CONTAINER_WORKSPACE,
                host=ip,
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
        await inst.runtime.client.close()
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

    def get_host(self, session_id: UUID | str) -> str | None:
        key = str(session_id)
        inst = self._instances.get(key)
        if inst:
            return inst.ip
        # Fallback: check if a container exists on Docker (e.g. after hot-reload)
        return self._get_container_ip_sync(f"{_NAME_PREFIX}{key[:12]}")

    def get_public_host(self, session_id: UUID | str) -> str | None:
        _ = session_id
        host = (settings.runtime_forward_public_host or "").strip()
        return host or "localhost"

    def resolve_port(self, session_id: UUID | str, internal_port: int) -> int | None:
        key = str(session_id)
        inst = self._instances.get(key)
        container_ref = inst.container_name if inst is not None else f"{_NAME_PREFIX}{key[:12]}"
        try:
            result = subprocess.run(
                ["docker", "port", container_ref, f"{int(internal_port)}/tcp"],
                capture_output=True,
                text=True,
            )
        except Exception:
            return None
        if result.returncode != 0:
            return None
        for line in (result.stdout or "").splitlines():
            text = line.strip()
            if not text:
                continue
            _, _, port_text = text.rpartition(":")
            try:
                return int(port_text)
            except ValueError:
                continue
        return None

    async def restart_browser(self, session_id: UUID | str, runtime: RuntimeInstance) -> None:
        _ = session_id
        await runtime.client.run(_DOCKER_BROWSER_RESTART_CMD, timeout=15)
        result = await runtime.client.run(_DOCKER_BROWSER_READY_CHECK_CMD, timeout=20)
        if result.exit_status != 0:
            raise RuntimeError("Browser CDP did not become ready")

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

    async def _remove_container(self, container_name: str) -> None:
        await asyncio.to_thread(
            subprocess.run,
            ["docker", "rm", "-f", container_name],
            capture_output=True,
        )

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
