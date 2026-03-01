import os
from ipaddress import ip_address
import uuid

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import AuditLog
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


def test_admin_estop_audit_and_config():
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
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        admin_token = login.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        user_token = _make_token(sub="user-1", role="agent")
        user_headers = {"Authorization": f"Bearer {user_token}"}

        estop_forbidden = client.post("/api/v1/admin/estop", headers=user_headers)
        assert estop_forbidden.status_code == 403

        estop = client.post("/api/v1/admin/estop", headers=admin_headers)
        assert estop.status_code == 200
        assert estop.json()["status"] == "activated"

        config_after_estop = client.get("/api/v1/admin/config", headers=admin_headers)
        assert config_after_estop.status_code == 200
        assert config_after_estop.json()["estop_active"] is True

        clear = client.delete("/api/v1/admin/estop", headers=admin_headers)
        assert clear.status_code == 200
        assert clear.json()["status"] == "deactivated"

        audits = client.get("/api/v1/admin/audit", headers=admin_headers)
        assert audits.status_code == 200
        assert audits.json()["total"] >= 2

        login_audits = client.get("/api/v1/admin/audit?action=auth.login", headers=admin_headers)
        assert login_audits.status_code == 200
        assert login_audits.json()["total"] >= 1
        assert all(item["action"] == "auth.login" for item in login_audits.json()["items"])

        config = client.get("/api/v1/admin/config", headers=admin_headers)
        assert config.status_code == 200
        payload = config.json()
        assert payload["estop_active"] is False
        assert payload["jwt_secret_key"] == "***"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_estop_state_persists_across_client_restart():
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
        first_client = TestClient(app)
        first_login = first_client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
        )
        first_headers = {"Authorization": f"Bearer {first_login.json()['access_token']}"}

        activated = first_client.post("/api/v1/admin/estop", headers=first_headers)
        assert activated.status_code == 200

        second_client = TestClient(app)
        second_login = second_client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
        )
        second_headers = {"Authorization": f"Bearer {second_login.json()['access_token']}"}
        config = second_client.get("/api/v1/admin/config", headers=second_headers)
        assert config.status_code == 200
        assert config.json()["estop_active"] is True
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_admin_audit_serializes_inet_ip_address():
    fake_db = FakeDB()
    fake_db.add(
        AuditLog(
            user_id="admin",
            action="admin.test",
            ip_address=ip_address("172.64.153.85"),
            status_code=200,
        )
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
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.get("/api/v1/admin/audit", headers=headers)
        assert response.status_code == 200
        payload = response.json()["items"]
        target = next((item for item in payload if item["action"] == "admin.test"), None)
        assert target is not None
        assert target["ip_address"] == "172.64.153.85"
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
