"""Per-session VNC reverse proxy."""

from __future__ import annotations

import asyncio
import logging
import traceback

import httpx
import websockets
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.services.runtime import get_runtime

logger = logging.getLogger(__name__)

router = APIRouter()

_NOVNC_PORT = 6080


def _get_container_ip(session_id: str) -> str | None:
    provider = get_runtime()
    if hasattr(provider, "get_container_ip"):
        return provider.get_container_ip(session_id)
    return None


@router.api_route(
    "/vnc/{session_id}/{path:path}",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def vnc_http_proxy(request: Request, session_id: str, path: str = "") -> Response:
    ip = _get_container_ip(session_id)
    if not ip:
        return Response(content="Runtime not found", status_code=404)

    upstream = f"http://{ip}:{_NOVNC_PORT}/{path}"
    qs = str(request.url.query)
    if qs:
        upstream += f"?{qs}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.request(
            request.method,
            upstream,
            headers={"Host": f"{ip}:{_NOVNC_PORT}"},
        )
    # Strip headers that block iframe embedding or WebSocket connections
    headers = dict(resp.headers)
    for h in ("x-frame-options", "content-security-policy", "content-length", "transfer-encoding"):
        headers.pop(h, None)

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=headers,
    )


@router.websocket("/vnc/{session_id}/websockify")
async def vnc_ws_proxy(websocket: WebSocket, session_id: str) -> None:
    ip = _get_container_ip(session_id)
    logger.warning("VNC WS proxy: session=%s ip=%s", session_id, ip)

    if not ip:
        await websocket.close(code=4004, reason="Runtime container not found")
        return

    # Accept without forcing a subprotocol — let the browser decide
    await websocket.accept()
    logger.warning("VNC WS proxy: accepted client websocket")

    upstream_ws = None
    try:
        upstream_url = f"ws://{ip}:{_NOVNC_PORT}/websockify"
        logger.warning("VNC WS proxy: connecting to upstream %s", upstream_url)

        upstream_ws = await asyncio.wait_for(
            websockets.connect(
                upstream_url,
                subprotocols=["binary"],
                max_size=2**22,
                open_timeout=10,
            ),
            timeout=15,
        )
        logger.warning("VNC WS proxy: upstream connected, subprotocol=%s", upstream_ws.subprotocol)

        async def _client_to_upstream() -> None:
            try:
                while True:
                    data = await websocket.receive_bytes()
                    await upstream_ws.send(data)
            except asyncio.CancelledError:
                raise
            except WebSocketDisconnect:
                logger.warning("VNC WS proxy: client disconnected")
            except Exception as e:
                logger.warning("VNC WS proxy: client_to_upstream error: %s", e)

        async def _upstream_to_client() -> None:
            try:
                async for message in upstream_ws:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)
            except asyncio.CancelledError:
                raise
            except WebSocketDisconnect:
                logger.warning("VNC WS proxy: upstream disconnected")
            except Exception as e:
                logger.warning("VNC WS proxy: upstream_to_client error: %s", e)

        done, pending = await asyncio.wait(
            [
                asyncio.create_task(_client_to_upstream()),
                asyncio.create_task(_upstream_to_client()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if done:
            await asyncio.gather(*done, return_exceptions=True)

    except Exception:
        logger.error("VNC WS proxy EXCEPTION:\n%s", traceback.format_exc())
    finally:
        if upstream_ws is not None:
            try:
                await asyncio.wait_for(upstream_ws.close(), timeout=1)
            except Exception:
                pass
        try:
            await asyncio.wait_for(websocket.close(), timeout=1)
        except Exception:
            pass
