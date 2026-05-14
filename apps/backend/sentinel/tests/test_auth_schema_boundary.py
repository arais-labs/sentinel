import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-with-32-bytes-min")

import jwt
from fastapi.testclient import TestClient

from app import main as app_main
from app.config import settings
from app.dependencies import get_db, get_manager_db
from app.main import app
from app.middleware.auth import Identity, create_access_token
from app.middleware.rate_limit import RateLimitMiddleware
from app.models import AuditLog
from app.models.manager import ManagerRevokedToken
from tests.fake_db import FakeDB


async def _noop_init_db() -> None:
    return None


def _install_db_overrides(
    *,
    manager_db: FakeDB,
    app_db: FakeDB,
) -> object:
    async def _override_get_db() -> AsyncGenerator[FakeDB, None]:
        yield app_db

    async def _override_get_manager_db() -> AsyncGenerator[FakeDB, None]:
        yield manager_db

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_manager_db] = _override_get_manager_db
    old_init_db = app_main.init_db
    app_main.init_db = _noop_init_db
    RateLimitMiddleware._buckets.clear()
    return old_init_db


def _restore_overrides(old_init_db: object) -> None:
    app.dependency_overrides.clear()
    app_main.init_db = old_init_db


def _token_payload(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def test_instance_route_revocation_is_checked_in_manager_db() -> None:
    manager_db = FakeDB()
    app_db = FakeDB()
    access_token = create_access_token(Identity(user_id="admin", role="admin"))
    payload = _token_payload(access_token)
    manager_db.add(
        ManagerRevokedToken(
            jti=payload["jti"],
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    )

    old_init_db = _install_db_overrides(manager_db=manager_db, app_db=app_db)
    try:
        client = TestClient(app)
        response = client.get(
            "/api/v1/instances/main/sessions",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 401
    finally:
        _restore_overrides(old_init_db)


def test_logout_revokes_tokens_in_manager_db_not_instance_db() -> None:
    manager_db = FakeDB()
    app_db = FakeDB()
    access_token = create_access_token(Identity(user_id="admin", role="admin"))

    old_init_db = _install_db_overrides(manager_db=manager_db, app_db=app_db)
    try:
        client = TestClient(app)
        response = client.delete(
            "/api/v1/auth/session",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        assert len(manager_db.storage[ManagerRevokedToken]) == 1
        assert app_db.storage[ManagerRevokedToken] == []
    finally:
        _restore_overrides(old_init_db)


def test_login_does_not_write_app_audit_log_to_manager_db() -> None:
    manager_db = FakeDB()
    app_db = FakeDB()

    old_init_db = _install_db_overrides(manager_db=manager_db, app_db=app_db)
    try:
        client = TestClient(app)
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin"},
        )
        assert response.status_code == 200
        assert manager_db.storage[AuditLog] == []
    finally:
        _restore_overrides(old_init_db)
