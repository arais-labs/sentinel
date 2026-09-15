from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.database.initialization import _run_alembic_upgrade
from app.database.engine import create_database_engine
from app.database.instance_sessions import instance_session_registry
from app.models import Memory, Message, Session
from app.services.backup.engine import export_backup, import_backup
from app.services.instances import InstanceRegistryService
from app.services.memory.search import MemorySearchService


@pytest.mark.asyncio
async def test_instance_isolation_rename_backup_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    app_engine = create_database_engine(settings.manager_database_url)
    await _run_alembic_upgrade(
        ini_name="alembic.manager.ini", database_url=settings.manager_database_url
    )
    service = InstanceRegistryService()
    try:
        async with async_sessionmaker(app_engine, expire_on_commit=False)() as registry:
            first = await service.create_instance(registry, name="first")
            second = await service.create_instance(registry, name="second")
            first_path = settings.database_path(first.database_name)
            second_path = settings.database_path(second.database_name)
            assert first_path.exists() and second_path.exists()
            assert first_path != second_path
            assert first_path.name == second_path.name == "instance.sqlite"
            assert first.database_name == str(first.id)
            assert (first_path.parent / "attachments").is_dir()
            factory = instance_session_registry.session_factory(first.database_name)
            async with factory() as db:
                session = Session(user_id="local", status="active", title="SQLite")
                db.add(session)
                await db.flush()
                db.add(Message(session_id=session.id, role="user", content="Remember this"))
                db.add(
                    Memory(
                        content="SQLite instance isolation",
                        category="project",
                        embedding=[1.0, 0.0],
                        session_id=session.id,
                    )
                )
                await db.commit()
                archive = await export_backup(
                    db,
                    instance_name=first.name,
                    items=["sessions", "memories"],
                    passphrase="a test passphrase",
                )
            other_factory = instance_session_registry.session_factory(second.database_name)
            async with other_factory() as db:
                assert (await db.execute(select(Session))).scalars().all() == []
                await import_backup(db, archive, "a test passphrase")
                assert len((await db.execute(select(Message))).scalars().all()) == 1
                results = await MemorySearchService().search(db, "isolation", category="project")
                assert results[0].memory.content == "SQLite instance isolation"
                assert results[0].memory.embedding == [1.0, 0.0]
            renamed = await service.rename_instance(registry, "first", "renamed")
            assert settings.database_path(renamed.database_name) == first_path
            assert first_path.exists()
            await service.delete_instance(registry, "renamed")
            assert not first_path.parent.exists()
            assert second_path.exists()
            # Disposed database connections reopen from the same file.
            await instance_session_registry.dispose(second.database_name)
            async with instance_session_registry.session_factory(second.database_name)() as db:
                assert len((await db.execute(select(Session))).scalars().all()) == 1
    finally:
        await instance_session_registry.dispose_all()
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_system_memory_key_remains_unique(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_root", tmp_path)
    url = settings.database_url(str(uuid4()))
    engine = create_database_engine(url)
    await _run_alembic_upgrade(ini_name="alembic.instance.ini", database_url=url)
    try:
        async with async_sessionmaker(engine)() as db:
            db.add_all(
                [
                    Memory(content="system", category="core", is_system=True, system_key="same")
                    for _ in range(2)
                ]
            )
            with pytest.raises(IntegrityError):
                await db.commit()
    finally:
        await engine.dispose()
