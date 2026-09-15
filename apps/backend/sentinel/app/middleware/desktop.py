"""Private transport boundary between Electron and its Python child process."""

from secrets import compare_digest

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class DesktopTransportMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        if not token:
            raise RuntimeError("SENTINEL_DESKTOP_TOKEN is required")
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            supplied = Headers(scope=scope).get("x-sentinel-desktop-token", "").encode()
            if not compare_digest(supplied, self.token):
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    await JSONResponse({"detail": "Desktop connection required"}, status_code=403)(
                        scope, receive, send
                    )
                return
        await self.app(scope, receive, send)
