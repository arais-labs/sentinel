from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.config import settings
from app.database import init_instance_db
from app.database.instance_sessions import instance_session_registry
from app.models.manager import SentinelInstance

_INSTANCE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
_DB_RE = re.compile(r"^sentinel_[a-z0-9_]+_[a-f0-9]{8}$")


class InstanceError(ValueError):
    pass


class InstanceAlreadyExistsError(InstanceError):
    pass


class InstanceNotFoundError(InstanceError):
    pass


class InvalidInstanceNameError(InstanceError):
    pass


@dataclass(frozen=True)
class InstanceDefaults:
    workspace_base: Path = Path(settings.instance_workspace_root)
    runtime_backend: str = "docker"


def normalize_instance_name(raw: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", raw.strip().lower())
    value = re.sub(r"-+", "-", value).strip("-")
    if not value or not _INSTANCE_RE.fullmatch(value):
        raise InvalidInstanceNameError("Instance name must use lowercase letters, numbers, and dashes")
    return value


def instance_database_name(name: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "instance"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    database_name = f"sentinel_{safe[:40]}_{digest}"
    if not _DB_RE.fullmatch(database_name):
        raise InvalidInstanceNameError("Could not derive a valid database name")
    return database_name


def _quote_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


class InstanceRegistryService:
    def __init__(self, *, defaults: InstanceDefaults | None = None):
        self._defaults = defaults or InstanceDefaults()

    async def list_instances(self, db: AsyncSession) -> list[SentinelInstance]:
        result = await db.execute(select(SentinelInstance).order_by(SentinelInstance.name))
        return list(result.scalars().all())

    async def get_instance(self, db: AsyncSession, name: str) -> SentinelInstance:
        normalized = normalize_instance_name(name)
        instance = await db.get(SentinelInstance, normalized)
        if instance is None:
            raise InstanceNotFoundError(f"Instance not found: {normalized}")
        return instance

    async def create_instance(
        self,
        db: AsyncSession,
        *,
        name: str,
        display_name: str | None = None,
        workspace_root: str | None = None,
        runtime_backend: str | None = None,
        runtime_config: dict[str, Any] | None = None,
    ) -> SentinelInstance:
        normalized = normalize_instance_name(name)
        existing = await db.get(SentinelInstance, normalized)
        if existing is not None:
            raise InstanceAlreadyExistsError(f"Instance already exists: {normalized}")

        database_name = instance_database_name(normalized)
        await instance_session_registry.dispose(database_name)
        if await self._database_exists(database_name):
            raise InstanceAlreadyExistsError(f"Instance database already exists: {database_name}")

        await self._create_database(database_name)
        try:
            await self._init_database(database_name)
            instance = SentinelInstance(
                name=normalized,
                database_name=database_name,
                display_name=display_name,
                workspace_root=workspace_root or str(self._defaults.workspace_base / normalized),
                runtime_backend=runtime_backend or self._defaults.runtime_backend,
                runtime_config_json=runtime_config or {},
            )
            db.add(instance)
            await db.commit()
            await db.refresh(instance)
            return instance
        except Exception:
            await db.rollback()
            await instance_session_registry.dispose(database_name)
            await self._drop_database(database_name)
            await instance_session_registry.dispose(database_name)
            raise

    async def update_instance(
        self,
        db: AsyncSession,
        name: str,
        *,
        display_name: str | None = None,
        workspace_root: str | None = None,
        runtime_backend: str | None = None,
        runtime_config: dict[str, Any] | None = None,
    ) -> SentinelInstance:
        instance = await self.get_instance(db, name)
        if display_name is not None:
            instance.display_name = display_name
        if workspace_root is not None:
            instance.workspace_root = workspace_root
        if runtime_backend is not None:
            instance.runtime_backend = runtime_backend
        if runtime_config is not None:
            instance.runtime_config_json = runtime_config
        await db.commit()
        await db.refresh(instance)
        return instance

    async def rename_instance(self, db: AsyncSession, old_name: str, new_name: str) -> SentinelInstance:
        instance = await self.get_instance(db, old_name)
        normalized_new = normalize_instance_name(new_name)
        if normalized_new == instance.name:
            return instance
        existing = await db.get(SentinelInstance, normalized_new)
        if existing is not None:
            raise InstanceAlreadyExistsError(f"Instance already exists: {normalized_new}")
        instance.name = normalized_new
        await db.commit()
        await db.refresh(instance)
        return instance

    async def delete_instance(self, db: AsyncSession, name: str) -> None:
        instance = await self.get_instance(db, name)
        database_name = instance.database_name
        await instance_session_registry.dispose(database_name)
        try:
            await self._drop_database(database_name)
        finally:
            await instance_session_registry.dispose(database_name)
        await db.delete(instance)
        await db.commit()

    async def _database_exists(self, database_name: str) -> bool:
        engine = self._admin_engine()
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                    {"database_name": database_name},
                )
                return result.scalar_one_or_none() == 1
        finally:
            await engine.dispose()

    async def _init_database(self, database_name: str) -> None:
        await init_instance_db(database_name)

    async def _create_database(self, database_name: str) -> None:
        if not _DB_RE.fullmatch(database_name):
            raise InvalidInstanceNameError(f"Invalid database name: {database_name}")
        engine = self._admin_engine()
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f"CREATE DATABASE {_quote_identifier(database_name)}"))
        finally:
            await engine.dispose()

    async def _drop_database(self, database_name: str) -> None:
        if not _DB_RE.fullmatch(database_name):
            raise InvalidInstanceNameError(f"Invalid database name: {database_name}")
        engine = self._admin_engine()
        try:
            async with engine.connect() as conn:
                quoted = _quote_identifier(database_name)
                await conn.execute(text(f"DROP DATABASE IF EXISTS {quoted} WITH (FORCE)"))
        finally:
            await engine.dispose()

    def _admin_engine(self) -> AsyncEngine:
        return create_async_engine(
            settings.database_url(settings.database_maintenance_name),
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
            echo=False,
        )
