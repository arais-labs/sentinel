from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.initialization import _run_alembic_upgrade
from app.database.engine import create_database_engine
from app.models import Memory, Message, Session


@pytest.mark.asyncio
async def test_workspace_schema_vectors_fts_and_foreign_keys(tmp_path):
    config = Settings(SENTINEL_STORAGE_ROOT=tmp_path)
    url = config.database_url(str(uuid4()))
    engine = create_database_engine(url)
    try:
        await _run_alembic_upgrade(ini_name="alembic.instance.ini", database_url=url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            assert (await db.execute(text("PRAGMA journal_mode"))).scalar_one() == "wal"
            assert (await db.execute(text("PRAGMA foreign_keys"))).scalar_one() == 1
            assert (await db.execute(text("PRAGMA busy_timeout"))).scalar_one() == 10000
            assert (
                await db.execute(text("SELECT vec_distance_cosine('[1,0]', '[1,0]')"))
            ).scalar_one() == 0
            session = Session(user_id="local", title="Workspace", status="active")
            db.add(session)
            await db.flush()
            memory = Memory(
                content="Running projects",
                title="Notes",
                category="project",
                embedding=[1.0, 0.0],
                session_id=session.id,
            )
            db.add(memory)
            await db.commit()
            assert memory.created_at.tzinfo == UTC
            assert memory.created_at <= datetime.now(UTC)
            assert (
                await db.execute(
                    text("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'run'")
                )
            ).scalar_one() == 1
            memory.content = "Updated projects"
            await db.commit()
            assert (
                await db.execute(
                    text("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'run'")
                )
            ).scalar_one() == 0
            assert (
                await db.execute(
                    text("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'updated'")
                )
            ).scalar_one() == 1
            await db.delete(session)
            await db.commit()
            await db.refresh(memory)
            assert memory.session_id is None
            assert memory.embedding == [1.0, 0.0]
            await db.delete(memory)
            await db.commit()
            assert (
                await db.execute(
                    text("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'updated'")
                )
            ).scalar_one() == 0
            db.add(Message(session_id=uuid4(), role="user", content="Invalid parent"))
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_manager_schema_and_workspace_isolation(tmp_path):
    config = Settings(SENTINEL_STORAGE_ROOT=tmp_path)
    engine = create_database_engine(config.manager_database_url)
    try:
        await _run_alembic_upgrade(
            ini_name="alembic.manager.ini", database_url=config.manager_database_url
        )
        async with engine.connect() as connection:
            tables = set(
                (
                    await connection.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                ).scalars()
            )
            assert "instances" in tables
            assert "sessions" not in tables
            assert (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one() == "0001_manager_initial"
        assert config.database_path(str(uuid4())) != config.database_path(str(uuid4()))
        for invalid in ["../escape", "main", "/tmp/workspace", str(uuid4()).upper()]:
            with pytest.raises(ValueError):
                config.database_path(invalid)
        assert (tmp_path / "app.sqlite").is_file()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_active_binding_unique_index_and_reopen(tmp_path):
    from app.models import SessionBinding

    config = Settings(SENTINEL_STORAGE_ROOT=tmp_path)
    url = config.database_url(str(uuid4()))
    engine = create_database_engine(url)
    try:
        await _run_alembic_upgrade(ini_name="alembic.instance.ini", database_url=url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            session = Session(user_id="local", title="Persistent", status="active")
            db.add(session)
            await db.flush()
            session_id = session.id
            db.add(
                SessionBinding(
                    user_id="local",
                    binding_type="telegram_group",
                    binding_key="owner",
                    session_id=session_id,
                    is_active=True,
                )
            )
            await db.commit()
            db.add(
                SessionBinding(
                    user_id="local",
                    binding_type="telegram_group",
                    binding_key="owner",
                    session_id=session_id,
                    is_active=True,
                )
            )
            with pytest.raises(IntegrityError):
                await db.commit()
            await db.rollback()
            db.add(
                SessionBinding(
                    user_id="local",
                    binding_type="telegram_group",
                    binding_key="owner",
                    session_id=session_id,
                    is_active=False,
                )
            )
            await db.commit()
    finally:
        await engine.dispose()
    reopened = create_database_engine(url)
    try:
        async with async_sessionmaker(reopened)() as db:
            loaded = await db.get(Session, session_id)
            assert loaded.title == "Persistent"
            assert loaded.id == session_id
            assert loaded.started_at.tzinfo == UTC
    finally:
        await reopened.dispose()


@pytest.mark.asyncio
async def test_wal_reader_and_serialized_writers(tmp_path):
    import asyncio

    config = Settings(SENTINEL_STORAGE_ROOT=tmp_path)
    url = config.database_url(str(uuid4()))
    first = create_database_engine(url)
    second = create_database_engine(url)
    try:
        async with first.begin() as connection:
            await connection.execute(
                text("CREATE TABLE counter (id INTEGER PRIMARY KEY, value INTEGER NOT NULL)")
            )
            await connection.execute(text("INSERT INTO counter VALUES (1, 0)"))
        async with first.connect() as writer:
            await writer.execute(text("BEGIN IMMEDIATE"))
            await writer.execute(text("UPDATE counter SET value = value + 1 WHERE id = 1"))
            async with second.connect() as reader:
                # WAL permits a reader while another connection has an uncommitted write.
                assert (await reader.execute(text("SELECT value FROM counter"))).scalar_one() == 0

            async def increment():
                async with second.begin() as next_writer:
                    await next_writer.execute(
                        text("UPDATE counter SET value = value + 1 WHERE id = 1")
                    )

            pending = asyncio.create_task(increment())
            await asyncio.sleep(0.05)
            assert not pending.done()
            await writer.commit()
            await asyncio.wait_for(pending, timeout=2)
        async with first.connect() as connection:
            assert (await connection.execute(text("SELECT value FROM counter"))).scalar_one() == 2
    finally:
        await first.dispose()
        await second.dispose()
