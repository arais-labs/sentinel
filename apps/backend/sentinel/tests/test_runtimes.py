from __future__ import annotations

import pytest

from app.models.manager import SentinelInstance
from app.schemas.runtimes import RuntimeCreateRequest, RuntimeUpdateRequest
from app.services.runtime.runtimes import (
    assign_instance_runtime,
    create_runtime,
    resolve_instance_runtime,
    runtime_response,
    update_runtime,
)
from app.services.runtime.target_secrets import decrypt_runtime_secret
from tests.fake_db import FakeDB


@pytest.mark.asyncio
async def test_runtime_secrets_are_encrypted() -> None:
    db = FakeDB()

    runtime = await create_runtime(
        db,
        RuntimeCreateRequest(
            name="local lima",
            provider="ssh",
            host="host.docker.internal",
            port=2222,
            username="sentinel",
            workspaces_dir="/srv/sentinel/runtime-workspaces",
            auth_type="private_key",
            private_key="PRIVATE KEY TEXT",
        ),
    )

    assert runtime.encrypted_secret != "PRIVATE KEY TEXT"
    assert decrypt_runtime_secret(runtime.encrypted_secret or "") == "PRIVATE KEY TEXT"
    response = runtime_response(runtime)
    assert not hasattr(response, "private_key")


@pytest.mark.asyncio
async def test_instance_runtime_assignment_resolves_secret() -> None:
    db = FakeDB()
    instance = SentinelInstance(name="main", database_name="sentinel_main_00000000")
    db.add(instance)
    runtime = await create_runtime(
        db,
        RuntimeCreateRequest(
            name="mac",
            provider="ssh",
            host="localhost",
            port=22,
            username="sentinel",
            workspaces_dir="/tmp/sentinel-workspaces",
            auth_type="password",
            password="secret-password",
        ),
    )

    updated = await assign_instance_runtime(db, instance_name="main", runtime_id=runtime.id)
    resolved = await resolve_instance_runtime(db, instance_name="main")

    assert updated.runtime_id == runtime.id
    assert resolved.host == "localhost"
    assert resolved.auth_type == "password"
    assert resolved.secret == "secret-password"


@pytest.mark.asyncio
async def test_runtime_update_rotates_secret() -> None:
    db = FakeDB()
    runtime = await create_runtime(
        db,
        RuntimeCreateRequest(
            name="target",
            provider="ssh",
            host="localhost",
            port=22,
            username="sentinel",
            workspaces_dir="/workspace-root",
            auth_type="password",
            password="old",
        ),
    )

    updated = await update_runtime(
        db,
        runtime.id,
        RuntimeUpdateRequest(auth_type="password", password="new"),
    )

    assert decrypt_runtime_secret(updated.encrypted_secret or "") == "new"
