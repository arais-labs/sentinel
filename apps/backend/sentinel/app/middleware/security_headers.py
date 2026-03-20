from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send


class SecurityHeadersMiddleware:
    """Add security headers to HTTP responses.

    Implemented as a raw ASGI middleware (not BaseHTTPMiddleware) to avoid
    breaking WebSocket connections.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Don't interfere with WebSocket or lifespan events
            await self.app(scope, receive, send)
            return

        # Skip security headers for VNC proxy paths (needs iframe embedding)
        path: str = scope.get("path", "")
        is_vnc = path.startswith("/vnc/")

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                # Convert to mutable list
                header_list = list(message.get("headers", []))

                if not is_vnc:
                    _setdefault(header_list, b"x-content-type-options", b"nosniff")
                    _setdefault(header_list, b"x-frame-options", b"DENY")
                    _setdefault(header_list, b"referrer-policy", b"no-referrer")
                    _setdefault(header_list, b"permissions-policy", b"camera=(), microphone=(), geolocation=()")
                    _setdefault(header_list, b"cache-control", b"no-store")

                message = {**message, "headers": header_list}

            await send(message)

        await self.app(scope, receive, send_with_headers)


def _setdefault(headers: list[tuple[bytes, bytes]], name: bytes, value: bytes) -> None:
    """Add header only if not already present."""
    for h_name, _ in headers:
        if h_name.lower() == name:
            return
    headers.append((name, value))
