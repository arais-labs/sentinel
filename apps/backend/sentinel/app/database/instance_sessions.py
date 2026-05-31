from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


class InstanceSessionRegistry:
    # TODO: add LRU / idle eviction when active instances exceed ~30
    # (15 max connections per engine × Postgres default max_connections=100).
    def __init__(self) -> None:
        self._engines: dict[str, AsyncEngine] = {}
        self._factories: dict[str, async_sessionmaker[AsyncSession]] = {}

    def session_factory(self, database_name: str) -> async_sessionmaker[AsyncSession]:
        factory = self._factories.get(database_name)
        if factory is not None:
            return factory

        engine = create_async_engine(
            settings.database_url(database_name),
            pool_pre_ping=True,
            echo=False,
        )
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        self._engines[database_name] = engine
        self._factories[database_name] = factory
        return factory

    async def session(self, database_name: str) -> AsyncGenerator[AsyncSession, None]:
        factory = self.session_factory(database_name)
        async with factory() as session:
            yield session

    async def dispose(self, database_name: str) -> None:
        self._factories.pop(database_name, None)
        engine = self._engines.pop(database_name, None)
        if engine is not None:
            await engine.dispose()

    async def dispose_all(self) -> None:
        engines = list(self._engines.values())
        self._engines.clear()
        self._factories.clear()
        for engine in engines:
            await engine.dispose()


instance_session_registry = InstanceSessionRegistry()
