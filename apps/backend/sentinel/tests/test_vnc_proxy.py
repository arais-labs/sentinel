from __future__ import annotations

import asyncio

from starlette.requests import Request

from app.routers import vnc_proxy


def _request(path: str, method: str = "GET") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def test_vnc_package_json_is_served_without_upstream(monkeypatch) -> None:
    monkeypatch.setattr(vnc_proxy, "_get_runtime_host", lambda _session_id: "192.168.2.33")
    monkeypatch.setattr(vnc_proxy, "_get_runtime_vnc_port", lambda _session_id: 16081)

    class _Client:
        def __init__(self, *args, **kwargs):
            raise AssertionError("upstream should not be called for package.json")

    monkeypatch.setattr(vnc_proxy.httpx, "AsyncClient", _Client)

    response = asyncio.run(
        vnc_proxy.vnc_http_proxy(
            _request("/vnc/session/package.json"),
            "session",
            "package.json",
        )
    )

    assert response.status_code == 200
    assert response.media_type == "application/json"
    assert b"novnc-proxy" in response.body


def test_vnc_package_json_head_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(vnc_proxy, "_get_runtime_host", lambda _session_id: "192.168.2.33")
    monkeypatch.setattr(vnc_proxy, "_get_runtime_vnc_port", lambda _session_id: 16081)

    class _Client:
        def __init__(self, *args, **kwargs):
            raise AssertionError("upstream should not be called for package.json")

    monkeypatch.setattr(vnc_proxy.httpx, "AsyncClient", _Client)

    response = asyncio.run(
        vnc_proxy.vnc_http_proxy(
            _request("/vnc/session/package.json", method="HEAD"),
            "session",
            "package.json",
        )
    )

    assert response.status_code == 200
    assert response.body == b""


def test_vnc_proxy_uses_provider_resolved_port(monkeypatch) -> None:
    monkeypatch.setattr(vnc_proxy, "_get_runtime_host", lambda _session_id: "192.168.2.33")
    monkeypatch.setattr(vnc_proxy, "_get_runtime_vnc_port", lambda _session_id: 16081)

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200
        headers = {"content-type": "text/html"}
        content = b"ok"

    class _Client:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, headers=None):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            return _Response()

    monkeypatch.setattr(vnc_proxy.httpx, "AsyncClient", _Client)

    response = asyncio.run(
        vnc_proxy.vnc_http_proxy(
            _request("/vnc/session/vnc.html"),
            "session",
            "vnc.html",
        )
    )

    assert response.status_code == 200
    assert captured["url"] == "http://192.168.2.33:16081/vnc.html"
    assert captured["headers"] == {"Host": "192.168.2.33:16081"}


def test_vnc_websockify_http_get_is_not_proxied(monkeypatch) -> None:
    monkeypatch.setattr(vnc_proxy, "_get_runtime_host", lambda _session_id: "192.168.2.33")
    monkeypatch.setattr(vnc_proxy, "_get_runtime_vnc_port", lambda _session_id: 16081)

    class _Client:
        def __init__(self, *args, **kwargs):
            raise AssertionError("websockify HTTP requests should not be proxied")

    monkeypatch.setattr(vnc_proxy.httpx, "AsyncClient", _Client)

    response = asyncio.run(
        vnc_proxy.vnc_http_proxy(
            _request("/vnc/session/websockify"),
            "session",
            "websockify",
        )
    )

    assert response.status_code == 426
    assert b"WebSocket upgrade" in response.body


def test_vnc_proxy_upstream_failure_returns_plain_502(monkeypatch) -> None:
    monkeypatch.setattr(vnc_proxy, "_get_runtime_host", lambda _session_id: "192.168.2.33")
    monkeypatch.setattr(vnc_proxy, "_get_runtime_vnc_port", lambda _session_id: 16081)

    class _Client:
        def __init__(self, *args, **kwargs):
            _ = args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method, url, headers=None):
            _ = method, url, headers
            raise vnc_proxy.httpx.ConnectError("boom")

    monkeypatch.setattr(vnc_proxy.httpx, "AsyncClient", _Client)

    response = asyncio.run(
        vnc_proxy.vnc_http_proxy(
            _request("/vnc/session/vnc.html"),
            "session",
            "vnc.html",
        )
    )

    assert response.status_code == 502
    assert response.media_type == "text/plain"
    assert b"VNC upstream unavailable" in response.body
