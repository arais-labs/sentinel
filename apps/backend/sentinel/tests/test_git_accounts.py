from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient


from app.dependencies import get_db, get_manager_db
from app.main import app
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import GitAccount, ToolApproval
from tests.fake_db import FakeDB


def test_git_accounts_crud_and_approval_resolution(monkeypatch):
    from app.routers import git
    from app.services.github_account import GitHubIdentity

    async def verify(_token):
        return GitHubIdentity(login="tester", name="Test User", email="alex@example.com")

    monkeypatch.setattr(git, "verify_github_token", verify)
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
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        admin_headers = {"x-sentinel-desktop-token": "test-desktop-transport-token"}

        created = client.post(
            "/api/v1/instances/main/git/accounts",
            json={
                "name": "Client GitHub",
                "host": "github.com",
                "scope_pattern": "example-org/*",
                "author_name": "Test User",
                "author_email": "alex@example.com",
                "token": "github-test-token",
            },
            headers=admin_headers,
        )
        assert created.status_code == 201
        account_id = created.json()["id"]
        assert created.json()["has_token"] is True
        assert created.json()["github_login"] == "tester"
        assert "github-test-token" not in created.text

        listed = client.get("/api/v1/instances/main/git/accounts", headers=admin_headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["name"] == "Client GitHub"

        updated = client.patch(
            f"/api/v1/instances/main/git/accounts/{account_id}",
            json={
                "scope_pattern": "example-org/sample-app*",
                "author_email": "ops@example.com",
            },
            headers=admin_headers,
        )
        assert updated.status_code == 200
        assert updated.json()["scope_pattern"] == "example-org/sample-app*"
        assert updated.json()["author_email"] == "ops@example.com"

        approval = ToolApproval(
            provider="git",
            tool_name="git",
            session_id=None,
            action="git.write",
            description="Execute an approval-gated git or supported gh write command inside the session workspace.",
            status="pending",
            requested_by="session:test",
            payload_json={"tool_name": "git"},
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        fake_db.add(approval)

        approvals = client.get(
            "/api/v1/instances/main/approvals?provider=git&status=pending", headers=admin_headers
        )
        assert approvals.status_code == 200
        assert approvals.json()["total"] == 1
        approval_id = approvals.json()["items"][0]["approval_id"]

        resolved = client.post(
            f"/api/v1/instances/main/approvals/git/{approval_id}/approve",
            json={"note": "approved"},
            headers=admin_headers,
        )
        assert resolved.status_code == 200
        assert resolved.json()["provider"] == "git"
        assert resolved.json()["status"] == "approved"
        assert resolved.json()["decision_note"] == "approved"

        removed = client.delete(
            f"/api/v1/instances/main/git/accounts/{account_id}", headers=admin_headers
        )
        assert removed.status_code == 200
        assert removed.json()["success"] is True
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init


def test_generic_approvals_routes_list_and_resolve_git_tool_approval():
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
    app.dependency_overrides[get_manager_db] = _override_get_db

    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        admin_headers = {"x-sentinel-desktop-token": "test-desktop-transport-token"}

        account = GitAccount(
            name="github-main",
            host="github.com",
            scope_pattern="exampleco/*",
            author_name="Bot",
            author_email="bot@arais.ai",
            token="github-test-token",
        )
        fake_db.add(account)
        fake_db.add(
            ToolApproval(
                provider="git",
                tool_name="git",
                session_id=None,
                action="git.write",
                description="Execute an approval-gated git or supported gh write command inside the session workspace.",
                status="pending",
                requested_by="session:test",
                payload_json={"tool_name": "git"},
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )

        approvals = client.get(
            "/api/v1/instances/main/approvals?status=pending", headers=admin_headers
        )
        assert approvals.status_code == 200
        body = approvals.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["provider"] == "git"
        assert item["pending"] is True
        assert item["can_resolve"] is True
        assert isinstance(item["approval_id"], str)

        resolved = client.post(
            f"/api/v1/instances/main/approvals/git/{item['approval_id']}/approve",
            json={"note": "approved from generic endpoint"},
            headers=admin_headers,
        )
        assert resolved.status_code == 200
        assert resolved.json()["provider"] == "git"
        assert resolved.json()["status"] == "approved"
        assert resolved.json()["pending"] is False
    finally:
        app.dependency_overrides.clear()
        app_main.init_db = old_init
