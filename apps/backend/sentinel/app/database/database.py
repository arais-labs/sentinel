from pathlib import Path
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.models import Base
from app.models.manager import ManagerBase

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
_STARTUP_SQL_DIR = Path(__file__).resolve().parent / "startup_sql"
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


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
    async with manager_engine.connect() as conn:
        async with conn.begin():
            await conn.run_sync(ManagerBase.metadata.create_all)


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
    """Create baseline app schema and apply SQL migrations/startup scripts."""
    instance_engine = create_async_engine(
        settings.database_url(database_name),
        pool_pre_ping=True,
        echo=False,
    )
    try:
        async with instance_engine.connect() as conn:
            await _init_app_schema(conn)
        session_factory = async_sessionmaker(instance_engine, class_=AsyncSession, expire_on_commit=False)
        await _seed_app_defaults(session_factory)
    finally:
        await instance_engine.dispose()


async def _init_app_schema(conn: AsyncConnection) -> None:
    """Create baseline app schema and apply SQL migrations/startup scripts."""
    async with conn.begin():
        await _run_startup_sql(conn, phase="pre")
        await conn.run_sync(Base.metadata.create_all)
        await _run_sql_migrations(conn)
        await _run_startup_sql(conn, phase="post")


async def _run_startup_sql(conn: AsyncConnection, *, phase: str) -> None:
    phase_dir = _STARTUP_SQL_DIR / phase
    if not phase_dir.is_dir():
        return
    for sql_file in sorted(phase_dir.glob("*.sql")):
        sql = sql_file.read_text(encoding="utf-8").strip()
        if not sql:
            continue
        for statement in _split_sql_statements(sql):
            await conn.execute(text(statement))


async def _run_sql_migrations(conn: AsyncConnection) -> None:
    if not _MIGRATIONS_DIR.is_dir():
        return

    for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        sql = sql_file.read_text(encoding="utf-8").strip()
        if not sql:
            continue
        for statement in _split_sql_statements(sql):
            await conn.execute(text(statement))


def _split_sql_statements(sql: str) -> list[str]:
    """Split SQL scripts into statements while preserving quoted/dollar-quoted blocks."""
    statements: list[str] = []
    current: list[str] = []

    in_single = False
    in_double = False
    dollar_tag: str | None = None

    i = 0
    length = len(sql)
    while i < length:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < length else ""

        if dollar_tag is not None:
            if sql.startswith(dollar_tag, i):
                current.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
            current.append(ch)
            i += 1
            continue

        if in_single:
            current.append(ch)
            if ch == "'" and nxt == "'":
                current.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_single = False
            i += 1
            continue

        if in_double:
            current.append(ch)
            if ch == '"' and nxt == '"':
                current.append(nxt)
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            current.append(ch)
            i += 1
            continue

        if ch == '"':
            in_double = True
            current.append(ch)
            i += 1
            continue

        if ch == "$":
            j = i + 1
            while j < length and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < length and sql[j] == "$":
                tag = sql[i : j + 1]
                dollar_tag = tag
                current.append(tag)
                i = j + 1
                continue

        if ch == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(f"{statement};")
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail if tail.endswith(";") else f"{tail};")
    return statements


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
