"""Per-session BrowserManager pool.

Each agent session gets its own BrowserManager connected to the
Chromium instance running inside the session's runtime container
via Chrome DevTools Protocol (CDP).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from uuid import UUID

from app.services.browser.manager import BrowserManager

if TYPE_CHECKING:
    from app.services.runtime.docker import DockerRuntimeProvider
    from app.services.runtime.provider import SSHRuntimeProvider

logger = logging.getLogger(__name__)

_CHROMIUM_RESTART_CMD = (
    "pkill -f '/usr/lib/chromium/chromium' || true; "
    "rm -f /home/sentinel/.config/chromium/SingletonLock "
    "/home/sentinel/.config/chromium/SingletonSocket "
    "/home/sentinel/.config/chromium/SingletonCookie 2>/dev/null || true; "
    "if ! pgrep -f 'socat TCP-LISTEN:9223' >/dev/null; then "
    "nohup socat TCP-LISTEN:9223,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9222 "
    ">/tmp/chromium-socat.log 2>&1 & "
    "fi; "
    "nohup su - sentinel -c "
    "\"DISPLAY=:99 chromium "
    "--no-sandbox "
    "--disable-gpu "
    "--disable-dev-shm-usage "
    "--remote-debugging-address=0.0.0.0 "
    "--remote-debugging-port=9222 "
    "--disable-blink-features=AutomationControlled "
    "--no-first-run "
    "--no-default-browser-check "
    "--window-size=1920,1080 "
    "about:blank\" "
    ">/tmp/chromium-reset.log 2>&1 &"
)
_CDP_PORT = 9223


class BrowserPool:
    """Manages per-session BrowserManager instances."""

    def __init__(self) -> None:
        self._managers: dict[str, BrowserManager] = {}

    async def get(self, session_id: UUID | str) -> BrowserManager:
        """Get or create a BrowserManager for the given session.

        On first call for a session, creates a new BrowserManager connected
        to the runtime container's CDP endpoint.
        """
        key = str(session_id)

        from app.services.runtime import get_runtime

        provider = get_runtime()
        runtime = await provider.ensure(session_id)

        manager = self._managers.get(key)
        if manager is not None:
            try:
                await manager.ensure_connected()
                return manager
            except Exception:
                logger.warning("BrowserManager for session %s became unhealthy; recreating it", key, exc_info=True)
                await self.remove(session_id)

        # Get container IP for CDP connection
        ip: str | None = None
        if hasattr(provider, "get_container_ip"):
            ip = provider.get_container_ip(session_id)
        if not ip:
            # For remote SSH, use the SSH host
            ip = runtime.ssh._host

        cdp_endpoint = f"http://{ip}:{_CDP_PORT}"
        manager = BrowserManager(cdp_endpoint=cdp_endpoint)
        try:
            await manager.ensure_connected()
        except Exception:
            logger.warning(
                "BrowserManager could not attach to session %s at %s; restarting Chromium with CDP",
                key,
                cdp_endpoint,
                exc_info=True,
            )
            await manager.close()
            await runtime.ssh.run(_CHROMIUM_RESTART_CMD, timeout=15)
            last_error: Exception | None = None
            for _ in range(10):
                try:
                    manager = BrowserManager(cdp_endpoint=cdp_endpoint)
                    await manager.ensure_connected()
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    await manager.close()
                    await asyncio.sleep(0.5)
            else:
                raise RuntimeError(
                    f"Browser CDP did not become ready for session {key}"
                ) from last_error
        self._managers[key] = manager
        logger.info("Created BrowserManager for session %s at %s", key, cdp_endpoint)
        return manager

    async def remove(self, session_id: UUID | str) -> None:
        """Close and remove the BrowserManager for a session."""
        key = str(session_id)
        manager = self._managers.pop(key, None)
        if manager is not None:
            try:
                await manager.close()
            except Exception:
                logger.debug("Error closing BrowserManager for session %s", key, exc_info=True)

    async def close_all(self) -> None:
        """Close all managed BrowserManagers."""
        for key in list(self._managers):
            await self.remove(key)
