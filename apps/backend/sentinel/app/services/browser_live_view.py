from __future__ import annotations

import socket
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import Request

from app.config import settings


def is_live_view_available() -> bool:
    if not settings.browser_live_view_enabled:
        return False
    timeout = max(settings.browser_live_probe_timeout_ms, 50) / 1000.0
    try:
        with socket.create_connection((settings.browser_live_host, settings.browser_live_port), timeout=timeout):
            return True
    except OSError:
        return False


def _origin_base_from_url(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunparse((parsed.scheme, parsed.netloc, "/vnc/vnc.html", "", "", ""))


def _normalize_resize_mode(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"scale", "remote", "off"}:
        return raw
    # Backward compatibility with older/local wording.
    if raw in {"local", "fit"}:
        return "scale"
    # Safe default: fit viewport to container in embedded mode.
    return "scale"


def build_live_view_url(request: Request) -> str:
    public_url = (settings.browser_live_public_url or "").strip()
    if public_url:
        base = public_url
    else:
        origin_base = _origin_base_from_url(request.headers.get("origin"))
        if not origin_base:
            origin_base = _origin_base_from_url(request.headers.get("referer"))

        if origin_base:
            base = origin_base
        else:
            parsed = urlparse(str(request.base_url))
            scheme = parsed.scheme or "http"

            # If request is coming through a gateway/proxy (different port than app port),
            # build a URL that stays on the same public origin and hits the proxied /vnc path.
            if parsed.netloc and parsed.port not in {None, settings.app_port}:
                base = urlunparse((scheme, parsed.netloc, "/vnc/vnc.html", "", "", ""))
            else:
                host = settings.browser_live_host
                netloc = f"{host}:{settings.browser_live_port}"
                base = urlunparse((scheme, netloc, settings.browser_live_path, "", "", ""))

    parsed_base = urlparse(base)
    path = parsed_base.path or settings.browser_live_path
    if not path.startswith("/"):
        path = f"/{path}"

    query = dict(parse_qsl(parsed_base.query, keep_blank_values=True))
    query.setdefault("autoconnect", "1" if settings.browser_live_autoconnect else "0")
    query.setdefault("resize", _normalize_resize_mode(settings.browser_live_resize))
    query.setdefault("view_only", "1" if settings.browser_live_view_only else "0")

    password = (settings.browser_vnc_password or "").strip()
    if password:
        query.setdefault("password", password)

    return urlunparse(
        (
            parsed_base.scheme or "http",
            parsed_base.netloc,
            path,
            "",
            urlencode(query, doseq=True),
            "",
        )
    )
