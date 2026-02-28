from pathlib import Path
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.models import Base

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
_STARTUP_SQL_DIR = Path(__file__).resolve().parent / "startup_sql"
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


async def init_db() -> None:
    """Create baseline schema and apply SQL migrations/startup scripts."""
    async with engine.connect() as conn:
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


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
