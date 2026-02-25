import os
import tempfile
import uuid

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
os.environ.setdefault("DEV_TOKEN", "sentinel-dev-token")
os.environ.setdefault("TOOL_FILE_READ_BASE_DIR", "/tmp")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
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


def test_tools_registry_and_execution():
    fake_db = FakeDB()

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

        detail = client.get("/api/v1/tools/file_read", headers=headers)
        assert detail.status_code == 200
        schema = detail.json()["parameters_schema"]
        assert "path" in schema["properties"]

        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
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
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_tool_auth_required():
    fake_db = FakeDB()

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
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_shell_exec_no_injection():
    fake_db = FakeDB()

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
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_file_read_path_traversal_blocked():
    fake_db = FakeDB()

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
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_http_request_ssrf_blocked():
    fake_db = FakeDB()

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
        app.dependency_overrides.clear()
        app_main.init_db = old_init
