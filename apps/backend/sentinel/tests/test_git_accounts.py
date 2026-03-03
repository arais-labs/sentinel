from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import GitAccount, GitPushApproval
from tests.fake_db import FakeDB


def _make_token(*, sub: str, role: str = "agent", agent_id: str = "agent-test") -> str:
    secret = os.getenv("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
    return jwt.encode(
        {
            "sub": sub,
            "role": role,
            "agent_id": agent_id,
            "exp": 1_999_999_999,
            "iat": 1_771_810_000,
            "jti": str(uuid.uuid4()),
            "token_type": "access",
        },
        secret,
        algorithm="HS256",
    )


def test_git_accounts_crud_and_push_approval_resolution():
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
        admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        created = client.post(
            "/api/v1/git/accounts",
            json={
                "name": "Client GitHub",
                "host": "github.com",
                "scope_pattern": "arais-labs/*",
                "author_name": "Alexandre",
                "author_email": "alex@example.com",
                "token_read": "ghr_read_123",
                "token_write": "ghw_write_456",
            },
            headers=admin_headers,
        )
        assert created.status_code == 201
        account_id = created.json()["id"]
        assert created.json()["has_read_token"] is True
        assert created.json()["has_write_token"] is True

        listed = client.get("/api/v1/git/accounts", headers=admin_headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["name"] == "Client GitHub"

        updated = client.patch(
            f"/api/v1/git/accounts/{account_id}",
            json={
                "scope_pattern": "arais-labs/sentinel*",
                "author_email": "ops@example.com",
            },
            headers=admin_headers,
        )
        assert updated.status_code == 200
        assert updated.json()["scope_pattern"] == "arais-labs/sentinel*"
        assert updated.json()["author_email"] == "ops@example.com"

        approval = GitPushApproval(
            account_id=uuid.UUID(account_id),
            session_id=None,
            repo_url="https://github.com/arais-labs/sentinel.git",
            remote_name="origin",
            command="git push origin main",
            status="pending",
            requested_by="session:test",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        fake_db.add(approval)

        approvals = client.get("/api/v1/git/push-approvals?status=pending", headers=admin_headers)
        assert approvals.status_code == 200
        assert approvals.json()["total"] == 1
        approval_id = approvals.json()["items"][0]["id"]

        resolved = client.post(
            f"/api/v1/git/push-approvals/{approval_id}/approve",
            json={"note": "approved"},
            headers=admin_headers,
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "approved"
        assert resolved.json()["decision_note"] == "approved"

        removed = client.delete(f"/api/v1/git/accounts/{account_id}", headers=admin_headers)
        assert removed.status_code == 200
        assert removed.json()["success"] is True
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_git_routes_require_admin_role():
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
        agent_headers = {"Authorization": f"Bearer {_make_token(sub='agent-1', role='agent')}"}
        response = client.get("/api/v1/git/accounts", headers=agent_headers)
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
