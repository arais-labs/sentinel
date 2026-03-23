"""Per-session BrowserManager pool.

Each agent session gets its own BrowserManager connected to the
Chromium instance running inside the session's runtime container
via Chrome DevTools Protocol (CDP).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from app.services.browser.manager import BrowserManager

if TYPE_CHECKING:
    from app.services.runtime.docker import DockerRuntimeProvider
    from app.services.runtime.provider import SSHRuntimeProvider

logger = logging.getLogger(__name__)

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
        if key in self._managers:
            return self._managers[key]

        from app.services.runtime import get_runtime

        provider = get_runtime()
        runtime = await provider.ensure(session_id)

        # Get container IP for CDP connection
        ip: str | None = None
        if hasattr(provider, "get_container_ip"):
            ip = provider.get_container_ip(session_id)
        if not ip:
            # For remote SSH, use the SSH host
            ip = runtime.ssh._host

        cdp_endpoint = f"http://{ip}:{_CDP_PORT}"
        manager = BrowserManager(cdp_endpoint=cdp_endpoint)
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
