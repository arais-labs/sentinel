from __future__ import annotations

import asyncio
import shutil
from uuid import uuid4
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
import app.database.initialization as database_initialization
from app.database.instance_sessions import instance_session_registry
from app.models.manager import SentinelInstance

_INSTANCE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")


class InstanceError(ValueError):
    pass


class InstanceAlreadyExistsError(InstanceError):
    pass


class InstanceNotFoundError(InstanceError):
    pass


class InvalidInstanceNameError(InstanceError):
    pass


def normalize_instance_name(raw: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", raw.strip().lower())
    value = re.sub(r"-+", "-", value).strip("-")
    if not value or not _INSTANCE_RE.fullmatch(value):
        raise InvalidInstanceNameError(
            "Instance name must use lowercase letters, numbers, and dashes"
        )
    return value


class InstanceRegistryService:
    async def list_instances(self, db: AsyncSession) -> list[SentinelInstance]:
        result = await db.execute(select(SentinelInstance).order_by(SentinelInstance.name))
        return list(result.scalars().all())

    async def _find_by_name(self, db: AsyncSession, normalized: str) -> SentinelInstance | None:
        result = await db.execute(
            select(SentinelInstance).where(SentinelInstance.name == normalized)
        )
        return result.scalar_one_or_none()

    async def get_instance(self, db: AsyncSession, name: str) -> SentinelInstance:
        normalized = normalize_instance_name(name)
        instance = await self._find_by_name(db, normalized)
        if instance is None:
            raise InstanceNotFoundError(f"Instance not found: {normalized}")
        return instance

    async def create_instance(
        self,
        db: AsyncSession,
        *,
        name: str,
        display_name: str | None = None,
        appearance: dict | None = None,
    ) -> SentinelInstance:
        normalized = normalize_instance_name(name)
        instance_id = uuid4()
        database_name = str(instance_id)

        existing = await self._find_by_name(db, normalized)
        if existing is not None:
            raise InstanceAlreadyExistsError(f"Instance already exists: {normalized}")

        instance = SentinelInstance(
            id=instance_id,
            name=normalized,
            database_name=database_name,
            display_name=display_name,
            appearance=appearance or {},
        )
        db.add(instance)
        try:
            # UNIQUE(name) is the serialization point for concurrent creates.
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise InstanceAlreadyExistsError(f"Instance already exists: {normalized}")
        await db.refresh(instance)

        await instance_session_registry.dispose(database_name)
        try:
            await self._create_database(database_name)
            await self._init_database(database_name)
        except Exception:
            try:
                await db.delete(instance)
                await db.commit()
            except Exception:
                await db.rollback()
            await instance_session_registry.dispose(database_name)
            await self._drop_database(database_name)
            raise
        return instance

    async def update_instance(
        self,
        db: AsyncSession,
        name: str,
        *,
        display_name: str | None = None,
        appearance: dict | None = None,
    ) -> SentinelInstance:
        instance = await self.get_instance(db, name)
        if display_name is not None:
            instance.display_name = display_name
        if appearance is not None:
            instance.appearance = appearance
        await db.commit()
        await db.refresh(instance)
        return instance

    async def rename_instance(
        self, db: AsyncSession, old_name: str, new_name: str
    ) -> SentinelInstance:
        instance = await self.get_instance(db, old_name)
        normalized_new = normalize_instance_name(new_name)
        if normalized_new == instance.name:
            return instance
        existing = await self._find_by_name(db, normalized_new)
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

    async def _init_database(self, database_name: str) -> None:
        await database_initialization.init_instance_db(database_name)

    async def _create_database(self, database_name: str) -> None:
        directory = settings.database_path(database_name).parent
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=False, mode=0o700)
        await asyncio.to_thread((directory / "attachments").mkdir, mode=0o700)

    async def _drop_database(self, database_name: str) -> None:
        directory = settings.database_path(database_name).parent
        if directory.exists():
            await asyncio.to_thread(shutil.rmtree, directory)
