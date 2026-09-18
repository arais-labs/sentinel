from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.runtime import migration_inputs


@pytest.mark.asyncio
async def test_completed_migrations_never_read_old_client_registries(monkeypatch):
    session = SimpleNamespace(
        request=AsyncMock(return_value={"inputs": [], "target": ["001_worker_ownership"]})
    )
    bridge = AsyncMock()
    monkeypatch.setattr(migration_inputs.workspace_containers, "local_request", bridge)
    assert await migration_inputs.prepare(session, uuid4()) == (["001_worker_ownership"], {})
    bridge.assert_not_called()


@pytest.mark.asyncio
async def test_pending_migration_collects_existing_references_through_local_bridge(monkeypatch):
    machine = uuid4()
    row = SimpleNamespace(
        id=uuid4(),
        name="Project",
        directory="/project",
        distribution="ubuntu",
        development_tools=["git"],
    )

    @asynccontextmanager
    async def factory():
        yield SimpleNamespace(scalars=AsyncMock(return_value=[row]))

    monkeypatch.setattr(
        migration_inputs.instance_runtime_context_registry,
        "all",
        lambda: [SimpleNamespace(session_factory=factory)],
    )
    bridge = AsyncMock(return_value={"001_worker_ownership": {"workspaces": {}}})
    monkeypatch.setattr(migration_inputs.workspace_containers, "local_request", bridge)
    session = SimpleNamespace(
        request=AsyncMock(
            return_value={"inputs": ["001_worker_ownership"], "target": ["001_worker_ownership"]}
        )
    )
    target, inputs = await migration_inputs.prepare(session, machine)
    assert target == ["001_worker_ownership"]
    assert "001_worker_ownership" in inputs
    bridge.assert_awaited_once_with(
        "runtime_migration_inputs",
        machine=str(machine),
        references=[
            {
                "id": str(row.id),
                "name": row.name,
                "project": row.directory,
                "distribution": "ubuntu",
                "tools": ["git"],
            }
        ],
    )


@pytest.mark.asyncio
async def test_unknown_input_contract_stops_upgrade():
    session = SimpleNamespace(
        request=AsyncMock(return_value={"inputs": ["future"], "target": ["future"]})
    )
    with pytest.raises(ValueError, match="Update Sentinel"):
        await migration_inputs.prepare(session, uuid4())
