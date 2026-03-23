from __future__ import annotations

import os
import sys
import types
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")
if "asyncssh" not in sys.modules:
    async def _asyncssh_connect_stub(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("asyncssh stub should not be called in git account tests")

    sys.modules["asyncssh"] = types.SimpleNamespace(
        SSHClientConnection=object,
        Error=RuntimeError,
        connect=_asyncssh_connect_stub,
    )

from app.dependencies import get_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import GitAccount, Message, Session, ToolApproval
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


def test_git_accounts_crud_and_approval_resolution():
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

        approval = ToolApproval(
            provider="git_exec",
            tool_name="git_exec",
            session_id=None,
            action="git_exec.run_write",
            description="Execute an approval-gated git or supported gh write command inside the session workspace.",
            status="pending",
            requested_by="session:test",
            payload_json={"tool_name": "git_exec"},
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        fake_db.add(approval)

        approvals = client.get("/api/v1/approvals?provider=git_exec&status=pending", headers=admin_headers)
        assert approvals.status_code == 200
        assert approvals.json()["total"] == 1
        approval_id = approvals.json()["items"][0]["approval_id"]

        resolved = client.post(
            f"/api/v1/approvals/git_exec/{approval_id}/approve",
            json={"note": "approved"},
            headers=admin_headers,
        )
        assert resolved.status_code == 200
        assert resolved.json()["provider"] == "git_exec"
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


def test_generic_approvals_routes_list_and_resolve_git_exec_tool_approval():
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

        account = GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="exampleco/*",
            author_name="Bot",
            author_email="bot@arais.ai",
            token_read="ghr_read_token_123",
            token_write="ghw_write_token_456",
        )
        fake_db.add(account)
        fake_db.add(
            ToolApproval(
                provider="git_exec",
                tool_name="git_exec",
                session_id=None,
                action="git_exec.run_write",
                description="Execute an approval-gated git or supported gh write command inside the session workspace.",
                status="pending",
                requested_by="session:test",
                payload_json={"tool_name": "git_exec"},
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )

        approvals = client.get("/api/v1/approvals?status=pending", headers=admin_headers)
        assert approvals.status_code == 200
        body = approvals.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["provider"] == "git_exec"
        assert item["pending"] is True
        assert item["can_resolve"] is True
        assert isinstance(item["approval_id"], str)

        resolved = client.post(
            f"/api/v1/approvals/git_exec/{item['approval_id']}/approve",
            json={"note": "approved from generic endpoint"},
            headers=admin_headers,
        )
        assert resolved.status_code == 200
        assert resolved.json()["provider"] == "git_exec"
        assert resolved.json()["status"] == "approved"
        assert resolved.json()["pending"] is False
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
