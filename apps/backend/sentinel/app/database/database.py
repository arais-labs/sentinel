from collections.abc import AsyncGenerator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.config import settings
from app.database.engine import create_database_engine

manager_engine = create_database_engine(
    settings.manager_database_url,
)
ManagerSessionLocal = async_sessionmaker(
    manager_engine, class_=AsyncSession, expire_on_commit=False
)
_current_session_factory: ContextVar[async_sessionmaker[AsyncSession] | None] = ContextVar(
    "current_db_session_factory",
    default=None,
)


class ContextualSessionFactory:
    """Select the active instance database for background runtime operations."""

    def __call__(self, *args, **kwargs):
        factory = _current_session_factory.get() or ManagerSessionLocal
        return factory(*args, **kwargs)


engine = manager_engine
AsyncSessionLocal = ContextualSessionFactory()


@contextmanager
def runtime_db_session_factory(
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> Iterator[None]:
    token = _current_session_factory.set(session_factory)
    try:
        yield
    finally:
        _current_session_factory.reset(token)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with ManagerSessionLocal() as session:
        yield session
