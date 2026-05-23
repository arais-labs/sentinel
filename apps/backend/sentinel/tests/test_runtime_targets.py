from __future__ import annotations

import pytest

from app.models.manager import SentinelInstance
from app.schemas.runtime_targets import RuntimeSSHTargetCreateRequest, RuntimeSSHTargetUpdateRequest
from app.services.runtime.target_secrets import decrypt_runtime_secret
from app.services.runtime.targets import (
    assign_instance_runtime_target,
    create_runtime_target,
    resolve_instance_runtime_target,
    runtime_target_response,
    update_runtime_target,
)
from tests.fake_db import FakeDB


@pytest.mark.asyncio
async def test_runtime_target_secrets_are_encrypted() -> None:
    db = FakeDB()

    target = await create_runtime_target(
        db,
        RuntimeSSHTargetCreateRequest(
            name="local lima",
            host="host.docker.internal",
            port=2222,
            username="sentinel",
            workspaces_dir="/srv/sentinel/runtime-workspaces",
            auth_type="private_key",
            private_key="PRIVATE KEY TEXT",
        ),
    )

    assert target.encrypted_secret != "PRIVATE KEY TEXT"
    assert decrypt_runtime_secret(target.encrypted_secret) == "PRIVATE KEY TEXT"
    response = runtime_target_response(target)
    assert not hasattr(response, "private_key")


@pytest.mark.asyncio
async def test_instance_runtime_target_assignment_resolves_secret() -> None:
    db = FakeDB()
    instance = SentinelInstance(name="main", database_name="sentinel_main_00000000")
    db.add(instance)
    target = await create_runtime_target(
        db,
        RuntimeSSHTargetCreateRequest(
            name="mac",
            host="localhost",
            port=22,
            username="sentinel",
            workspaces_dir="/tmp/sentinel-workspaces",
            auth_type="password",
            password="secret-password",
        ),
    )

    updated = await assign_instance_runtime_target(db, instance_name="main", target_id=target.id)
    resolved = await resolve_instance_runtime_target(db, instance_name="main")

    assert updated.runtime_target_id == target.id
    assert resolved.host == "localhost"
    assert resolved.auth_type == "password"
    assert resolved.secret == "secret-password"


@pytest.mark.asyncio
async def test_runtime_target_update_rotates_secret() -> None:
    db = FakeDB()
    target = await create_runtime_target(
        db,
        RuntimeSSHTargetCreateRequest(
            name="target",
            host="localhost",
            port=22,
            username="sentinel",
            workspaces_dir="/workspace-root",
            auth_type="password",
            password="old",
        ),
    )

    updated = await update_runtime_target(
        db,
        target.id,
        RuntimeSSHTargetUpdateRequest(auth_type="password", password="new"),
    )

    assert decrypt_runtime_secret(updated.encrypted_secret) == "new"
