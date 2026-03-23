from __future__ import annotations

import socket
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import Request

from app.config import settings


def is_runtime_available_for_session(session_id: str) -> bool:
    """Check if the runtime container's noVNC is reachable for a session."""
    if not settings.runtime_live_view_enabled:
        return False
    try:
        from app.services.runtime import get_runtime
        provider = get_runtime()
        if hasattr(provider, "get_container_ip"):
            ip = provider.get_container_ip(session_id)
            if not ip:
                return False
            timeout = max(settings.runtime_live_probe_timeout_ms, 50) / 1000.0
            with socket.create_connection((ip, 6080), timeout=timeout):
                return True
        return False
    except (OSError, Exception):
        return False


def build_runtime_view_url(request: Request, session_id: str | None = None) -> str:
    """Build the VNC URL -- routes through the API proxy for per-session containers."""
    origin_base = _origin_base_from_url(request.headers.get("origin"))
    if not origin_base:
        origin_base = _origin_base_from_url(request.headers.get("referer"))

    if origin_base:
        parsed = urlparse(origin_base)
        base = urlunparse((parsed.scheme, parsed.netloc, f"/vnc/{session_id}/vnc.html" if session_id else "/vnc/vnc.html", "", "", ""))
    else:
        parsed = urlparse(str(request.base_url))
        scheme = parsed.scheme or "http"
        path = f"/vnc/{session_id}/vnc.html" if session_id else "/vnc/vnc.html"
        base = urlunparse((scheme, parsed.netloc, path, "", "", ""))

    parsed_base = urlparse(base)
    query = dict(parse_qsl(parsed_base.query, keep_blank_values=True))
    query.setdefault("autoconnect", "1" if settings.runtime_live_autoconnect else "0")
    query.setdefault("resize", _normalize_resize_mode(settings.runtime_live_resize))
    query.setdefault("view_only", "1" if settings.runtime_live_view_only else "0")
    # Tell noVNC the correct websocket path so it connects through our proxy
    if session_id:
        query.setdefault("path", f"vnc/{session_id}/websockify")

    password = (settings.runtime_vnc_password or "").strip()
    if password:
        query.setdefault("password", password)

    return urlunparse(
        (
            parsed_base.scheme or "http",
            parsed_base.netloc,
            parsed_base.path,
            "",
            urlencode(query, doseq=True),
            "",
        )
    )


def _origin_base_from_url(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return raw


def _normalize_resize_mode(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"scale", "remote", "off"}:
        return raw
    if raw in {"local", "fit"}:
        return "scale"
    return "scale"
