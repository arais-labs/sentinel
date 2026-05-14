"""Per-session VNC reverse proxy."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback

import httpx
import websockets
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from app.services.runtime import get_runtime
from app.services.runtime.base import RuntimeServiceEndpoint

logger = logging.getLogger(__name__)

router = APIRouter()

_NOVNC_PORT = 6080


def _get_runtime_vnc_endpoint(session_id: str):
    provider = get_runtime()
    if hasattr(provider, "get_internal_endpoint"):
        endpoint = provider.get_internal_endpoint(session_id, _NOVNC_PORT)
        if endpoint is not None:
            return endpoint
    host = provider.get_host(session_id)
    if not host:
        return None
    return RuntimeServiceEndpoint(host=host, port=_NOVNC_PORT)


@router.api_route(
    "/vnc/{session_id}/{path:path}",
    methods=["GET", "HEAD"],
    include_in_schema=False,
)
async def vnc_http_proxy(request: Request, session_id: str, path: str = "") -> Response:
    endpoint = _get_runtime_vnc_endpoint(session_id)
    if endpoint is None:
        return Response(content="Runtime not found", status_code=404)

    if path == "websockify":
        return Response(
            content="VNC websocket endpoint requires a WebSocket upgrade",
            status_code=426,
            media_type="text/plain",
        )

    if path == "package.json":
        payload = json.dumps({"name": "novnc-proxy", "version": "0.0.0"})
        return Response(content="" if request.method == "HEAD" else payload, status_code=200, media_type="application/json")

    upstream = f"http://{endpoint.host}:{endpoint.port}/{path}"
    qs = str(request.url.query)
    if qs:
        upstream += f"?{qs}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(
                request.method,
                upstream,
                headers={"Host": f"{endpoint.host}:{endpoint.port}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("VNC HTTP proxy failed: session=%s path=%s upstream=%s error=%s", session_id, path, upstream, exc)
        return Response(
            content=f"VNC upstream unavailable for {path or 'vnc.html'}",
            status_code=502,
            media_type="text/plain",
        )
    # Strip headers that block iframe embedding or WebSocket connections
    headers = dict(resp.headers)
    for h in ("x-frame-options", "content-security-policy", "content-length", "transfer-encoding", "server", "date"):
        headers.pop(h, None)

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=headers,
    )


@router.websocket("/vnc/{session_id}/websockify")
async def vnc_ws_proxy(websocket: WebSocket, session_id: str) -> None:
    endpoint = _get_runtime_vnc_endpoint(session_id)

    if endpoint is None:
        await websocket.close(code=4004, reason="Runtime container not found")
        return

    requested_protocols = [
        protocol.strip()
        for protocol in (websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if protocol.strip()
    ]
    await websocket.accept(subprotocol="binary" if "binary" in requested_protocols else None)

    upstream_ws = None
    try:
        upstream_url = f"ws://{endpoint.host}:{endpoint.port}/websockify"

        upstream_ws = await asyncio.wait_for(
            websockets.connect(
                upstream_url,
                subprotocols=["binary"],
                max_size=2**22,
                open_timeout=10,
            ),
            timeout=15,
        )

        async def _client_to_upstream() -> None:
            try:
                while True:
                    data = await websocket.receive_bytes()
                    await upstream_ws.send(data)
            except asyncio.CancelledError:
                raise
            except WebSocketDisconnect:
                logger.debug("VNC WS proxy client disconnected")
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
                logger.debug("VNC WS proxy upstream disconnected")
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
