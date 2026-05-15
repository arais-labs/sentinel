from collections.abc import AsyncGenerator
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

manager_engine = create_async_engine(
    settings.manager_database_url,
    pool_pre_ping=True,
    echo=False,
)
ManagerSessionLocal = async_sessionmaker(manager_engine, class_=AsyncSession, expire_on_commit=False)
_current_session_factory: ContextVar[async_sessionmaker[AsyncSession] | None] = ContextVar(
    "current_db_session_factory",
    default=None,
)


class ContextualSessionFactory:
    """Compatibility session factory that follows the active runtime DB context."""

    def __call__(self, *args, **kwargs):
        factory = _current_session_factory.get() or ManagerSessionLocal
        return factory(*args, **kwargs)

# Transitional aliases. These keep existing imports alive while the routers and
# services are moved from one global app DB to explicit manager/instance DBs.
engine = manager_engine
AsyncSessionLocal = ContextualSessionFactory()
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_SCRIPT_LOCATIONS = {
    "alembic.manager.ini": _BACKEND_ROOT / "db" / "alembic" / "manager",
    "alembic.instance.ini": _BACKEND_ROOT / "db" / "alembic" / "instance",
}


@contextmanager
def runtime_db_session_factory(
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> Iterator[None]:
    token = _current_session_factory.set(session_factory)
    try:
        yield
    finally:
        _current_session_factory.reset(token)


async def init_db() -> None:
    """Create manager schema.

    App schemas are initialized per instance with `init_instance_db`.
    """
    await init_manager_db()


async def init_manager_db() -> None:
    await ensure_database_exists(settings.database_manager_name)
    await _run_alembic_upgrade(
        ini_name="alembic.manager.ini",
        database_url=settings.manager_database_url,
    )


async def ensure_database_exists(database_name: str) -> None:
    maintenance_engine = create_async_engine(
        settings.database_url(settings.database_maintenance_name),
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
        echo=False,
    )
    try:
        async with maintenance_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": database_name},
            )
            if result.scalar_one_or_none() == 1:
                return
            await conn.execute(text(f"CREATE DATABASE {_quote_identifier(database_name)}"))
    finally:
        await maintenance_engine.dispose()


async def init_instance_db(database_name: str) -> None:
    """Apply instance database schema migrations and seed app defaults."""
    instance_engine = create_async_engine(
        settings.database_url(database_name),
        pool_pre_ping=True,
        echo=False,
    )
    try:
        await _run_alembic_upgrade(
            ini_name="alembic.instance.ini",
            database_url=settings.database_url(database_name),
        )
        session_factory = async_sessionmaker(instance_engine, class_=AsyncSession, expire_on_commit=False)
        await _seed_app_defaults(session_factory)
    finally:
        await instance_engine.dispose()


async def _run_alembic_upgrade(*, ini_name: str, database_url: str) -> None:
    config = Config(str(_BACKEND_ROOT / ini_name))
    script_location = _ALEMBIC_SCRIPT_LOCATIONS[ini_name]
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(command.upgrade, config, "head")


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


async def _seed_app_defaults(session_factory: async_sessionmaker[AsyncSession]) -> None:
    from sqlalchemy import select

    from app.models.araios import AraiosPermission
    from app.services.araios.permissions import combined_agent_permissions

    async with session_factory() as db:
        existing_result = await db.execute(select(AraiosPermission))
        existing_actions = {row.action for row in existing_result.scalars().all()}
        for action, level in combined_agent_permissions().items():
            if action not in existing_actions:
                db.add(AraiosPermission(action=action, level=level))
        await db.commit()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with ManagerSessionLocal() as session:
        yield session
