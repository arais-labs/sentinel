from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.main import app
from app.models import AuditLog
from app.routers.admin import list_audit_logs
from tests.fake_db import FakeDB
from tests.helpers import install_fake_db_overrides, restore_test_app


def test_admin_config():
    old_init = install_fake_db_overrides(app_db=FakeDB())
    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        response = client.get("/api/v1/instances/main/admin/config")
        assert response.status_code == 200
        assert "jwt_secret_key" not in response.json()
        assert response.json()["app_name"]
    finally:
        restore_test_app(old_init)


@pytest.mark.asyncio
async def test_instance_audit_sql_pagination():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(AuditLog.__table__.create)
        async with async_sessionmaker(engine)() as db:
            timestamp = datetime(2026, 1, 1, tzinfo=UTC)
            for index, user, action, seconds in [
                (1, "alice", "read", 0),
                (2, "alice", "read", 0),
                (3, "bob", "read", 1),
                (4, "alice", "write", 2),
            ]:
                db.add(
                    AuditLog(
                        id=UUID(int=index),
                        timestamp=timestamp + timedelta(seconds=seconds),
                        user_id=user,
                        action=action,
                        ip_address="172.64.153.85",
                    )
                )
            await db.commit()

            async def page(action=None, user_id=None, limit=2, offset=0):
                return await list_audit_logs(
                    action=action, user_id=user_id, limit=limit, offset=offset, db=db
                )

            first = await page()
            assert first.total == 4
            assert [item.id for item in first.items] == [UUID(int=4), UUID(int=3)]
            second = await page(offset=2)
            assert second.total == 4
            assert [item.id for item in second.items] == [UUID(int=2), UUID(int=1)]
            assert second.items[1].ip_address == "172.64.153.85"
            assert (await page(action="read")).total == 3
            assert (await page(user_id="alice")).total == 3
            filtered = await page(action="read", user_id="alice", limit=1, offset=1)
            assert filtered.total == 2
            assert [item.id for item in filtered.items] == [UUID(int=1)]
            assert (await page(offset=100)).items == []
            missing = await page(action="absent")
            assert missing.total == 0 and missing.items == []
    finally:
        await engine.dispose()


def test_manager_audit_route_removed():
    assert "/api/v1/admin/audit" not in app.openapi()["paths"]
