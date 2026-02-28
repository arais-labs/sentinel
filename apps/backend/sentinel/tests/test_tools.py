import asyncio
import json
import os
import tempfile
import uuid
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")
os.environ.setdefault("TOOL_FILE_READ_BASE_DIR", "/tmp")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models.system import SystemSetting
from app.services.tools import ToolExecutor, build_default_registry
from app.services.tools.builtin import _validate_public_hostname
from tests.fake_db import FakeDB


def _make_token(*, sub: str, role: str = "agent", agent_id: str = "agent-test") -> str:
    secret = os.getenv("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
    return jwt.encode(
        {
            "sub": sub,
            "role": role,
            "agent_id": agent_id,
            "exp": 1999999999,
            "iat": 1771810000,
            "jti": str(uuid.uuid4()),
            "token_type": "access",
        },
        secret,
        algorithm="HS256",
    )


class _FakeSessionContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSessionFactory:
    def __init__(self, db):
        self._db = db

    def __call__(self):
        return _FakeSessionContext(self._db)


def _install_app_tool_runtime(fake_db: FakeDB):
    previous_registry = getattr(app.state, "tool_registry", None)
    previous_executor = getattr(app.state, "tool_executor", None)
    registry = build_default_registry(session_factory=_FakeSessionFactory(fake_db))
    app.state.tool_registry = registry
    app.state.tool_executor = ToolExecutor(registry)
    return previous_registry, previous_executor


def _restore_app_tool_runtime(previous_registry, previous_executor) -> None:
    app.state.tool_registry = previous_registry
    app.state.tool_executor = previous_executor


def test_tools_registry_and_execution():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        listed = client.get("/api/v1/tools", headers=headers)
        assert listed.status_code == 200
        names = {item["name"] for item in listed.json()["items"]}
        assert {"file_read", "http_request", "shell_exec"} <= names
        assert "browser_reset" in names
        assert "araios_api" in names

        detail = client.get("/api/v1/tools/file_read", headers=headers)
        assert detail.status_code == 200
        schema = detail.json()["parameters_schema"]
        assert "path" in schema["properties"]

        with tempfile.NamedTemporaryFile("w", delete=False, dir="/tmp") as handle:
            handle.write("tool execution ok")
            file_path = handle.name

        run = client.post(
            "/api/v1/tools/file_read/execute",
            json={"input": {"path": file_path}},
            headers=headers,
        )
        assert run.status_code == 200
        assert "tool execution ok" in run.json()["result"]["content"]

        invalid = client.post(
            "/api/v1/tools/file_read/execute",
            json={"input": {}},
            headers=headers,
        )
        assert invalid.status_code == 422

        estop = client.post("/api/v1/admin/estop", headers=headers)
        assert estop.status_code == 200
        blocked = client.post(
            "/api/v1/tools/shell_exec/execute",
            json={"input": {"command": "echo blocked"}},
            headers=headers,
        )
        assert blocked.status_code == 403
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_tool_auth_required():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        response = client.get("/api/v1/tools")
        assert response.status_code == 401

        user_token = _make_token(sub="standard-user")
        auth = {"Authorization": f"Bearer {user_token}"}
        response = client.get("/api/v1/tools", headers=auth)
        assert response.status_code == 200
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_shell_exec_no_injection():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        run = client.post(
            "/api/v1/tools/shell_exec/execute",
            json={"input": {"command": "echo hello; echo injected"}},
            headers=headers,
        )
        assert run.status_code == 200
        assert run.json()["result"]["stdout"] == "hello\n"
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_file_read_path_traversal_blocked():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post(
            "/api/v1/tools/file_read/execute",
            json={"input": {"path": "/etc/passwd"}},
            headers=headers,
        )
        assert response.status_code == 422
        assert "outside allowed directory" in response.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_http_request_ssrf_blocked():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        response = client.post(
            "/api/v1/tools/http_request/execute",
            json={"input": {"url": "http://127.0.0.1:8000/health", "method": "GET"}},
            headers=headers,
        )
        assert response.status_code == 422
        assert "SSRF blocked" in response.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_http_request_ssrf_allow_hosts_bypasses_private_check():
    previous = os.environ.get("SSRF_ALLOW_HOSTS")
    os.environ["SSRF_ALLOW_HOSTS"] = "araios-backend"
    try:
        asyncio.run(_validate_public_hostname("araios-backend"))
    finally:
        if previous is None:
            os.environ.pop("SSRF_ALLOW_HOSTS", None)
        else:
            os.environ["SSRF_ALLOW_HOSTS"] = previous


class _FakeHttpxResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.headers = {"content-type": "application/json"}
        self.content = json.dumps(payload).encode("utf-8")
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class _FakeAraiOSAsyncClient:
    def __init__(self, *_args, **_kwargs):
        self._request_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json: dict | None = None):
        if url.endswith("/platform/auth/token"):
            assert json is not None
            assert json.get("api_key", "").startswith("sk-arais-agent-")
            return _FakeHttpxResponse(
                200,
                {
                    "access_token": "test-access-token",
                    "refresh_token": "test-refresh-token",
                    "expires_in": 3600,
                },
            )
        if url.endswith("/platform/auth/refresh"):
            return _FakeHttpxResponse(
                200,
                {
                    "access_token": "test-access-token-refresh",
                    "refresh_token": "test-refresh-token-next",
                    "expires_in": 3600,
                },
            )
        return _FakeHttpxResponse(404, {"detail": "not found"})

    async def request(self, method: str, url: str, **kwargs):
        self._request_count += 1
        headers = kwargs.get("headers", {})
        assert headers.get("Authorization", "").startswith("Bearer test-access-token")
        return _FakeHttpxResponse(
            200, {"ok": True, "method": method, "url": url, "attempt": self._request_count}
        )


def test_araios_api_tool_executes_with_configured_integration():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)
    fake_db.add(
        SystemSetting(key="araios_integration_base_url", value="http://araios-backend:9000")
    )
    fake_db.add(
        SystemSetting(key="araios_integration_agent_api_key", value="sk-arais-agent-test-token")
    )

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        with patch("app.services.tools.builtin.httpx.AsyncClient", _FakeAraiOSAsyncClient):
            response = client.post(
                "/api/v1/tools/araios_api/execute",
                json={"input": {"path": "/api/agent", "method": "GET"}},
                headers=headers,
            )
        assert response.status_code == 200
        body = response.json()["result"]["body"]
        assert body["ok"] is True
        assert body["url"] == "http://araios-backend:9000/api/agent"
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_araios_api_tool_requires_integration_configuration():
    fake_db = FakeDB()
    previous_registry, previous_executor = _install_app_tool_runtime(fake_db)

    async def _override_get_db():
        yield fake_db

    async def _noop_init_db():
        return None

    from app import main as app_main

    old_init = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    app.dependency_overrides[get_db] = _override_get_db

    try:
        client = TestClient(app)
        login = client.post("/api/v1/auth/token", json={"araios_token": "sentinel-dev-token"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.post(
            "/api/v1/tools/araios_api/execute",
            json={"input": {"path": "/api/agent", "method": "GET"}},
            headers=headers,
        )
        assert response.status_code == 422
        assert "AraiOS integration is not configured" in response.json()["error"]["message"]
    finally:
        _restore_app_tool_runtime(previous_registry, previous_executor)
        app.dependency_overrides.clear()
        app_main.init_db = old_init
