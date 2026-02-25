from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create required extensions and tables for local/dev startup."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS parent_session_id UUID"))
        await conn.execute(text("ALTER TABLE triggers ADD COLUMN IF NOT EXISTS user_id VARCHAR(100)"))
        await conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS tool_call_id VARCHAR(100)"))
        await conn.execute(text("ALTER TABLE messages ADD COLUMN IF NOT EXISTS tool_name VARCHAR(100)"))
        await conn.execute(text("ALTER TABLE memories ADD COLUMN IF NOT EXISTS parent_id UUID"))
        await conn.execute(text("ALTER TABLE memories ADD COLUMN IF NOT EXISTS title VARCHAR(200)"))
        await conn.execute(text("ALTER TABLE memories ADD COLUMN IF NOT EXISTS summary TEXT"))
        await conn.execute(text("ALTER TABLE memories ADD COLUMN IF NOT EXISTS importance INTEGER DEFAULT 0"))
        await conn.execute(text("ALTER TABLE memories ADD COLUMN IF NOT EXISTS pinned BOOLEAN DEFAULT FALSE"))
        await conn.execute(text("ALTER TABLE memories ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ"))
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_memories_content_tsv "
                "ON memories USING GIN (to_tsvector('english', content))"
            )
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_parent_id ON memories(parent_id)"))
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_memories_roots_rank "
                "ON memories(parent_id, pinned DESC, importance DESC, updated_at DESC)"
            )
        )
        try:
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_memories_embedding_ivfflat "
                    "ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
                )
            )
        except Exception:
            # IVFFlat can fail if pgvector index preconditions are not met yet (e.g. empty table).
            pass


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
