from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.manager import SentinelInstance
from app.schemas.runtimes import RuntimeCreateRequest, RuntimeUpdateRequest
from app.services.secrets import InvalidSecretValue
from app.services.runtime.runtimes import (
    RuntimeErrorBase,
    assign_instance_runtime,
    create_runtime,
    resolve_instance_runtime,
    runtime_config_status_detail,
    runtime_response,
    update_runtime,
)
from tests.fake_db import FakeDB


@pytest.mark.asyncio
async def test_runtime_secret_is_stored() -> None:
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

    assert runtime.encrypted_secret == "PRIVATE KEY TEXT"
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

    assert updated.encrypted_secret == "new"


def test_runtime_response_accepts_backend_status_detail() -> None:
    runtime = type(
        "RuntimeRow",
        (),
        {
            "id": uuid4(),
            "name": "target",
            "provider": "ssh",
            "status": "error",
            "profile": "ssh",
            "host": "localhost",
            "port": 22,
            "username": "sentinel",
            "workspaces_dir": "/workspace-root",
            "auth_type": "password",
            "provider_config": {},
            "provider_state": {},
            "last_job_id": None,
            "last_job_status": None,
            "created_at": None,
            "updated_at": None,
        },
    )()

    response = runtime_response(runtime, status_detail="backend diagnostic")

    assert response.status_detail == "backend diagnostic"


@pytest.mark.asyncio
async def test_invalid_secret_reports_runtime_detail_without_mutating_row() -> None:
    db = FakeDB()
    instance = SentinelInstance(name="main", database_name="sentinel_main_00000000")
    db.add(instance)
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
            password="secret-password",
        ),
    )
    runtime.encrypted_secret = InvalidSecretValue("test")
    instance.runtime_id = runtime.id

    with pytest.raises(RuntimeErrorBase):
        await resolve_instance_runtime(db, instance_name="main")

    assert runtime.status == "ready"
    assert runtime.auth_type == "password"
    assert runtime.provider_state == {}
    assert runtime_config_status_detail(runtime) == "Runtime credentials could not be decrypted."

    repaired = await update_runtime(
        db,
        runtime.id,
        RuntimeUpdateRequest(auth_type="password", password="new-secret"),
    )

    assert repaired.status == "ready"
    assert repaired.provider_state == {}
    assert runtime_config_status_detail(repaired) is None
