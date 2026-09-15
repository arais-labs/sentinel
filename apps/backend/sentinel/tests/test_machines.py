from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.manager import SentinelInstance
from app.schemas.machines import MachineCreateRequest, MachineUpdateRequest
from app.services.secrets import InvalidSecretValue
from app.services.runtime.machines import (
    ResolvedMachine,
    MachineErrorBase,
    create_machine,
    resolve_machine_secret,
    machine_config_status_detail,
    machine_response,
    update_machine,
)
from tests.fake_db import FakeDB


def _resolved(provider: str, auth_type: str, secret: str = "") -> ResolvedMachine:
    return ResolvedMachine(
        id=uuid4(),
        name=provider,
        provider=provider,
        host="127.0.0.1",
        port=22,
        username="tester",
        auth_type=auth_type,
        secret=secret,
        updated_at_marker="",
    )


def test_local_resolved_runtime_has_no_ssh_credentials() -> None:
    resolved = _resolved("local", "local")
    with pytest.raises(MachineErrorBase, match="no SSH credentials"):
        resolved.credentials()


def test_ssh_resolved_runtime_still_builds_credentials() -> None:
    creds = _resolved("ssh", "private_key", secret="KEYDATA").credentials()
    assert creds.host == "127.0.0.1"
    assert creds.private_key == "KEYDATA"
    assert creds.password is None


@pytest.mark.asyncio
async def test_runtime_secret_is_stored() -> None:
    db = FakeDB()

    runtime = await create_machine(
        db,
        MachineCreateRequest(
            name="remote machine",
            provider="ssh",
            host="runtime.example.test",
            port=2222,
            username="sentinel",
            auth_type="private_key",
            private_key="PRIVATE KEY TEXT",
        ),
    )

    assert runtime.encrypted_secret == "PRIVATE KEY TEXT"
    response = machine_response(runtime)
    assert not hasattr(response, "private_key")


@pytest.mark.asyncio
async def test_machine_resolves_secret() -> None:
    db = FakeDB()
    instance = SentinelInstance(name="main", database_name="sentinel_main_00000000")
    db.add(instance)
    runtime = await create_machine(
        db,
        MachineCreateRequest(
            name="mac",
            provider="ssh",
            host="localhost",
            port=22,
            username="sentinel",
            auth_type="password",
            password="secret-password",
        ),
    )

    resolved = resolve_machine_secret(runtime)

    assert resolved.id == runtime.id
    assert resolved.host == "localhost"
    assert resolved.auth_type == "password"
    assert resolved.secret == "secret-password"


@pytest.mark.asyncio
async def test_runtime_update_rotates_secret() -> None:
    db = FakeDB()
    runtime = await create_machine(
        db,
        MachineCreateRequest(
            name="target",
            provider="ssh",
            host="localhost",
            port=22,
            username="sentinel",
            auth_type="password",
            password="old",
        ),
    )

    updated = await update_machine(
        db,
        runtime.id,
        MachineUpdateRequest(auth_type="password", password="new"),
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
            "auth_type": "password",
            "provider_config": {},
            "provider_state": {},
            "last_job_id": None,
            "last_job_status": None,
            "created_at": None,
            "updated_at": None,
        },
    )()

    response = machine_response(runtime, status_detail="backend diagnostic")

    assert response.status_detail == "backend diagnostic"


@pytest.mark.asyncio
async def test_invalid_secret_reports_runtime_detail_without_mutating_row() -> None:
    db = FakeDB()
    instance = SentinelInstance(name="main", database_name="sentinel_main_00000000")
    db.add(instance)
    runtime = await create_machine(
        db,
        MachineCreateRequest(
            name="target",
            provider="ssh",
            host="localhost",
            port=22,
            username="sentinel",
            auth_type="password",
            password="secret-password",
        ),
    )
    runtime.encrypted_secret = InvalidSecretValue("test")

    with pytest.raises(MachineErrorBase):
        resolve_machine_secret(runtime)

    assert runtime.status == "ready"
    assert runtime.auth_type == "password"
    assert runtime.provider_state == {}
    assert machine_config_status_detail(runtime) == "Machine credentials could not be decrypted."

    repaired = await update_machine(
        db,
        runtime.id,
        MachineUpdateRequest(auth_type="password", password="new-secret"),
    )

    assert repaired.status == "ready"
    assert repaired.provider_state == {}
    assert machine_config_status_detail(repaired) is None


def test_runtime_capabilities_offer_ssh_and_local() -> None:
    from app.services.runtime.providers import MachineProviderService

    capabilities = MachineProviderService().capabilities()
    assert {item.provider for item in capabilities.providers} == {"ssh", "local"}
    ssh = next(item for item in capabilities.providers if item.provider == "ssh")
    assert ssh.available
    assert ssh.missing == []


@pytest.mark.asyncio
async def test_runtime_restart_names_span_instances_and_keep_unknown_workspaces(
    monkeypatch,
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from app.database.instance_sessions import instance_session_registry
    from app.services.runtime.machines import runtime_workspace_labels

    machine_id, first, second, unknown = uuid4(), uuid4(), uuid4(), uuid4()
    db = AsyncMock()
    db.execute.return_value = Mock(
        scalars=lambda: Mock(
            all=lambda: [
                SimpleNamespace(name="personal", database_name="personal_db"),
                SimpleNamespace(name="work", database_name="work_db"),
            ]
        )
    )
    candidates = {
        "personal_db": [(first, "Sample Project")],
        "work_db": [(second, "Sample Ubuntu")],
    }

    def factory(database_name):
        @asynccontextmanager
        async def session():
            instance_db = AsyncMock()
            instance_db.execute.return_value = Mock(all=lambda: candidates[database_name])
            yield instance_db
            query = instance_db.execute.call_args.args[0]
            assert query.compile().params["machine_id_1"] == machine_id

        return session

    monkeypatch.setattr(instance_session_registry, "session_factory", factory)
    labels = await runtime_workspace_labels(db, machine_id, [str(second), str(first), str(unknown)])
    assert labels == [
        {"id": str(second), "name": "Sample Ubuntu", "instance": "work"},
        {"id": str(first), "name": "Sample Project", "instance": "personal"},
        {"id": str(unknown), "name": "Unregistered workspace", "instance": None},
    ]
    assert len(await runtime_workspace_labels(db, machine_id, None)) == 2
