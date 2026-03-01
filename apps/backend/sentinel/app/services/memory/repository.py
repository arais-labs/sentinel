from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Memory


class MemoryRepository:
    async def list_all(self, db: AsyncSession) -> list[Memory]:
        result = await db.execute(select(Memory))
        return result.scalars().all()

    async def get_by_id(self, db: AsyncSession, memory_id: UUID) -> Memory | None:
        result = await db.execute(select(Memory).where(Memory.id == memory_id))
        return result.scalars().first()

    async def create(self, db: AsyncSession, memory: Memory) -> Memory:
        db.add(memory)
        await db.commit()
        await db.refresh(memory)
        return memory

    async def save(self, db: AsyncSession, memory: Memory) -> Memory:
        await db.commit()
        await db.refresh(memory)
        return memory

    async def delete_by_ids(self, db: AsyncSession, memories: list[Memory], ids: set[UUID]) -> None:
        for node in memories:
            if node.id in ids:
                await db.delete(node)
        await db.commit()
