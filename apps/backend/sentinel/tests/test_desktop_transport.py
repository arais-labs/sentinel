import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.middleware.desktop import DesktopTransportMiddleware


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(DesktopTransportMiddleware, token="private-child-token")

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.websocket("/stream")
    async def stream(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"delta": "hello"})
        await websocket.close()

    return TestClient(app)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-sentinel-desktop-token": "wrong"},
        {"authorization": "Bearer private-child-token"},
        {"cookie": "sentinel_access_token=private-child-token"},
    ],
)
def test_http_requires_private_transport_header(client, headers):
    assert client.get("/health", headers=headers).status_code == 403


def test_private_http_request_passes(client):
    assert client.get(
        "/health", headers={"x-sentinel-desktop-token": "private-child-token"}
    ).json() == {"ok": True}


def test_query_token_cannot_open_websocket(client):
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/stream?token=private-child-token"):
            pass
    assert error.value.code == 1008


def test_private_websocket_stream_passes(client):
    with client.websocket_connect(
        "/stream", headers={"x-sentinel-desktop-token": "private-child-token"}
    ) as socket:
        assert socket.receive_json() == {"delta": "hello"}


def test_empty_transport_secret_fails_closed():
    with pytest.raises(RuntimeError, match="SENTINEL_DESKTOP_TOKEN"):
        DesktopTransportMiddleware(FastAPI(), token="")
