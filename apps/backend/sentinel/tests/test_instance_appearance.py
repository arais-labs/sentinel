from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.initialization import _run_alembic_upgrade
from app.database.engine import create_database_engine
from app.services.instances import InstanceRegistryService


@pytest.mark.asyncio
async def test_appearance_fresh_registry_defaults_and_persists(tmp_path):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    BACKEND_ROOT = Path(__file__).resolve().parents[1]

    settings = Settings(SENTINEL_STORAGE_ROOT=tmp_path)
    config = Config(str(BACKEND_ROOT / "alembic.manager.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "db/alembic/manager"))
    config.set_main_option("sqlalchemy.url", settings.manager_database_url)
    # Initialize a fresh registry.
    import asyncio

    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_database_engine(settings.manager_database_url)
    identifier = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO instances (id, name, database_name, display_name) VALUES (:id, 'studio', :database, 'Studio')"
                ),
                {"id": identifier.hex, "database": str(identifier)},
            )
        await _run_alembic_upgrade(
            ini_name="alembic.manager.ini", database_url=settings.manager_database_url
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            service = InstanceRegistryService()
            instance = await service.get_instance(db, "studio")
            assert instance.appearance == {}
            await service.update_instance(
                db, "studio", appearance={"color": "#123456", "icon": "code"}
            )
    finally:
        await engine.dispose()
    reopened = create_database_engine(settings.manager_database_url)
    try:
        async with async_sessionmaker(reopened)() as db:
            instance = await InstanceRegistryService().get_instance(db, "studio")
            assert instance.appearance == {"color": "#123456", "icon": "code"}
            assert instance.database_name == str(identifier)
    finally:
        await reopened.dispose()
