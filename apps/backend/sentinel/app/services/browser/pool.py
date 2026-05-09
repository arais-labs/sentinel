"""Per-session BrowserManager pool.

Each agent session gets its own BrowserManager connected to the
Chromium instance running inside the session's runtime container
via Chrome DevTools Protocol (CDP).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from uuid import UUID

from app.services.browser.manager import BrowserManager

logger = logging.getLogger(__name__)
_CDP_PORT = 9223


class BrowserPool:
    """Manages per-session BrowserManager instances."""

    def __init__(self) -> None:
        self._managers: dict[str, BrowserManager] = {}

    def _normalize_cdp_host(self, host: str) -> str:
        raw = (host or "").strip()
        if not raw:
            return raw
        if raw == "localhost":
            return raw
        try:
            ipaddress.ip_address(raw)
            return raw
        except ValueError:
            pass
        try:
            resolved = socket.gethostbyname(raw)
        except OSError:
            return raw
        return resolved or raw

    async def _build_runtime_context(
        self, session_id: UUID | str
    ) -> tuple[str, object, object]:
        from app.services.runtime import get_runtime

        provider = get_runtime()
        runtime = await provider.ensure(session_id)

        ip: str | None = None
        if hasattr(provider, "get_host"):
            ip = provider.get_host(session_id)
        if not ip:
            ip = runtime.host
        ip = self._normalize_cdp_host(str(ip or ""))
        port = _CDP_PORT
        if hasattr(provider, "resolve_port"):
            resolved_port = provider.resolve_port(session_id, _CDP_PORT)
            if resolved_port:
                port = int(resolved_port)
        return f"http://{ip}:{port}", runtime, provider

    async def _connect_manager(self, key: str, cdp_endpoint: str) -> BrowserManager:
        last_error: Exception | None = None
        for _ in range(20):
            manager = BrowserManager(cdp_endpoint=cdp_endpoint)
            try:
                await manager.ensure_connected()
                self._managers[key] = manager
                logger.info("Created BrowserManager for session %s at %s", key, cdp_endpoint)
                return manager
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                await manager.close()
                await asyncio.sleep(0.25)
        raise RuntimeError(f"Browser CDP did not become ready for session {key}") from last_error

    async def _restart_remote_browser(
        self, key: str, runtime: object, provider: object, cdp_endpoint: str
    ) -> None:
        try:
            await provider.restart_browser(key, runtime)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Browser CDP readiness probe failed for session %s at %s",
                key,
                cdp_endpoint,
                exc_info=True,
            )
            raise RuntimeError(
                f"Browser CDP did not become ready for session {key}"
            ) from exc

    async def get(self, session_id: UUID | str) -> BrowserManager:
        """Get or create a BrowserManager for the given session.

        On first call for a session, creates a new BrowserManager connected
        to the runtime container's CDP endpoint.
        """
        key = str(session_id)
        cdp_endpoint, runtime, provider = await self._build_runtime_context(session_id)

        manager = self._managers.get(key)
        if manager is not None:
            try:
                await manager.ensure_connected()
                return manager
            except Exception:
                logger.warning("BrowserManager for session %s became unhealthy; recreating it", key, exc_info=True)
                await self.remove(session_id)
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
            await self._restart_remote_browser(key, runtime, provider, cdp_endpoint)
            return await self._connect_manager(key, cdp_endpoint)
        self._managers[key] = manager
        logger.info("Created BrowserManager for session %s at %s", key, cdp_endpoint)
        return manager

    async def reset(self, session_id: UUID | str) -> dict[str, object]:
        key = str(session_id)
        await self.remove(session_id)
        cdp_endpoint, runtime, provider = await self._build_runtime_context(session_id)
        await self._restart_remote_browser(key, runtime, provider, cdp_endpoint)
        manager = await self._connect_manager(key, cdp_endpoint)
        warm = await manager.warmup()
        return {
            "reset": True,
            "url": warm.get("url") or "about:blank",
            "title": warm.get("title") or "",
            "tab_id": warm.get("tab_id"),
            "cdp_endpoint": cdp_endpoint,
        }

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
