from __future__ import annotations

import pytest

from app.models.manager import SentinelInstance
from app.database.instance_sessions import InstanceSessionRegistry
from app.services.instances import (
    InstanceAlreadyExistsError,
    InstanceNotFoundError,
    InstanceRegistryService,
    instance_database_name,
    normalize_instance_name,
)
from tests.fake_db import FakeDB


class _TestRegistryService(InstanceRegistryService):
    def __init__(self):
        super().__init__()
        self.created: list[str] = []
        self.initialized: list[str] = []
        self.dropped: list[str] = []

    async def _database_exists(self, database_name: str) -> bool:
        return database_name in self.created

    async def _create_database(self, database_name: str) -> None:
        self.created.append(database_name)

    async def _init_database(self, database_name: str) -> None:
        self.initialized.append(database_name)

    async def _drop_database(self, database_name: str) -> None:
        self.dropped.append(database_name)


class _DisposableEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


def test_normalize_instance_name():
    assert normalize_instance_name("Main Instance") == "main-instance"
    assert normalize_instance_name(" client--a ") == "client-a"


def test_instance_database_name_is_stable():
    first = instance_database_name("main")
    second = instance_database_name("main")

    assert first == second
    assert first.startswith("sentinel_main_")


@pytest.mark.asyncio
async def test_instance_session_registry_caches_factory():
    registry = InstanceSessionRegistry()
    try:
        first = registry.session_factory("sentinel_main_0d6e4079")
        second = registry.session_factory("sentinel_main_0d6e4079")
        other = registry.session_factory("sentinel_other_d9298a10")

        assert first is second
        assert other is not first
    finally:
        await registry.dispose_all()


@pytest.mark.asyncio
async def test_instance_session_registry_disposes_one_database():
    registry = InstanceSessionRegistry()
    kept = _DisposableEngine()
    removed = _DisposableEngine()
    registry._engines["sentinel_main_0d6e4079"] = removed
    registry._engines["sentinel_other_d9298a10"] = kept
    registry._factories["sentinel_main_0d6e4079"] = object()
    registry._factories["sentinel_other_d9298a10"] = object()

    await registry.dispose("sentinel_main_0d6e4079")

    assert removed.disposed is True
    assert kept.disposed is False
    assert "sentinel_main_0d6e4079" not in registry._engines
    assert "sentinel_main_0d6e4079" not in registry._factories
    assert "sentinel_other_d9298a10" in registry._engines


@pytest.mark.asyncio
async def test_create_instance_creates_registry_row_and_database():
    db = FakeDB()
    service = _TestRegistryService()

    instance = await service.create_instance(db, name="Main")

    assert instance.name == "main"
    assert instance.database_name == instance_database_name("main")
    assert service.created == [instance.database_name]
    assert service.initialized == [instance.database_name]
    assert db.storage[SentinelInstance] == [instance]


@pytest.mark.asyncio
async def test_create_instance_rejects_duplicate_name():
    db = FakeDB()
    service = _TestRegistryService()
    await service.create_instance(db, name="main")

    with pytest.raises(InstanceAlreadyExistsError):
        await service.create_instance(db, name="main")


@pytest.mark.asyncio
async def test_rename_instance_keeps_database_name_stable():
    db = FakeDB()
    service = _TestRegistryService()
    instance = await service.create_instance(db, name="main")
    old_database = instance.database_name

    renamed = await service.rename_instance(db, "main", "client-a")

    assert renamed.name == "client-a"
    assert renamed.database_name == old_database


@pytest.mark.asyncio
async def test_delete_instance_removes_registry_row_and_drops_database():
    db = FakeDB()
    service = _TestRegistryService()
    instance = await service.create_instance(db, name="main")

    await service.delete_instance(db, "main")

    assert db.storage[SentinelInstance] == []
    assert service.dropped == [instance.database_name]


@pytest.mark.asyncio
async def test_get_missing_instance_raises():
    db = FakeDB()
    service = _TestRegistryService()

    with pytest.raises(InstanceNotFoundError):
        await service.get_instance(db, "main")


@pytest.mark.asyncio
async def test_concurrent_create_loser_does_not_drop_winners_database():
    """Regression for the create_instance race: under concurrency, two callers
    can both pass the pre-check (`_find_by_name`) before either commits. The
    UNIQUE constraint on instances.name fires on the second commit. Before the
    fix, the loser's except-branch ran `_drop_database(...)` and destroyed the
    winner's data. After the fix, the loser raises InstanceAlreadyExistsError
    without touching Postgres administrative state.
    """
    db = FakeDB()
    service = _TestRegistryService()

    winner = await service.create_instance(db, name="main")
    dropped_before = list(service.dropped)

    async def _bypass_precheck(_db, _normalized):
        return None

    original = service._find_by_name
    service._find_by_name = _bypass_precheck  # type: ignore[method-assign]
    try:
        with pytest.raises(InstanceAlreadyExistsError):
            await service.create_instance(db, name="main")
    finally:
        service._find_by_name = original  # type: ignore[method-assign]

    assert service.dropped == dropped_before, "loser must not drop winner's database"
    assert winner in db.storage[SentinelInstance], "winner's manager row must survive"
    assert winner.database_name in service.created
