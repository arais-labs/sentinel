"""Schema bootstrap and application defaults, above database session primitives."""

import asyncio
from pathlib import Path
from threading import Lock
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.config import settings
from app.database.engine import create_database_engine
from app.logging_context import apply_logging_config
from app.models.modules import Module, ModulePermission
from app.services.modules.builtins.documents.module import MODULE as documents
from app.services.modules.builtins.tasks.module import MODULE as tasks
from app.services.modules.permissions import combined_agent_permissions

_schema_lock = Lock()
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_SCRIPT_LOCATIONS = {
    "alembic.manager.ini": _BACKEND_ROOT / "db" / "alembic" / "manager",
    "alembic.instance.ini": _BACKEND_ROOT / "db" / "alembic" / "instance",
}


async def init_db() -> None:
    """Create manager schema.

    App schemas are initialized per instance with `init_instance_db`.
    """
    await init_manager_db()


async def init_manager_db() -> None:
    await _run_alembic_upgrade(
        ini_name="alembic.manager.ini",
        database_url=settings.manager_database_url,
    )


async def init_instance_db(database_name: str) -> None:
    """Apply instance database schema migrations and seed app defaults."""
    instance_engine = create_database_engine(
        settings.database_url(database_name),
    )
    try:
        await _run_alembic_upgrade(
            ini_name="alembic.instance.ini",
            database_url=settings.database_url(database_name),
        )
        session_factory = async_sessionmaker(
            instance_engine, class_=AsyncSession, expire_on_commit=False
        )
        await _seed_app_defaults(session_factory)
    finally:
        await instance_engine.dispose()


async def _run_alembic_upgrade(*, ini_name: str, database_url: str) -> None:
    config = Config(str(_BACKEND_ROOT / ini_name))
    script_location = _ALEMBIC_SCRIPT_LOCATIONS[ini_name]
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

    def upgrade() -> None:
        # Alembic shares a process-global context across its environment scripts.
        with _schema_lock:
            command.upgrade(config, "head")

    try:
        await asyncio.to_thread(upgrade)
    finally:
        apply_logging_config()


async def _seed_app_defaults(session_factory: async_sessionmaker[AsyncSession]) -> None:

    async with session_factory() as db:
        # Native record handlers use module_records, whose module_name references
        # modules.name. Register their storage parents as part of instance setup.
        for definition in (documents, tasks):
            if await db.get(Module, definition.name) is None:
                values = definition.to_dict()
                values.pop("grouped_tool")  # Runtime-only; not a storage column.
                db.add(Module(**values))
        existing_result = await db.execute(select(ModulePermission))
        existing_actions = {row.action for row in existing_result.scalars().all()}
        for action, level in combined_agent_permissions().items():
            if action not in existing_actions:
                db.add(ModulePermission(action=action, level=level))
        await db.commit()
