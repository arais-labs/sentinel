from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.database import get_db_session


def get_settings() -> object:
    """Dependency accessor to keep settings wiring centralized."""
    return settings


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session
