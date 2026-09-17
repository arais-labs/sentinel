"""Voice is one hidden session per instance, always run in Voice mode."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from uuid import uuid4

from app.models import Base, Message, Session, SessionKind, Workspace
from app.services.agent.agent_modes import (
    AgentMode,
    effective_agent_mode,
    list_agent_mode_definitions,
)
from app.services.voice.session import reset_voice_session, voice_session


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reset_discards_the_conversation_and_keeps_one_voice_session(db):
    first = await voice_session(db)
    db.add(Message(session_id=first.id, role="user", content="Old request"))
    await db.commit()
    workspace = Workspace(name="Backend", directory="/work", machine_id=uuid4())
    db.add(workspace)
    await db.flush()
    first.workspace_id = workspace.id
    await db.commit()
    second = await reset_voice_session(db)
    assert second.id != first.id and second.kind == SessionKind.VOICE
    assert (
        second.workspace_id == workspace.id
    ), "The workspace is a Voice setting and survives a reset"
    sessions = (await db.execute(Session.__table__.select())).all()
    assert len(sessions) == 1
    assert not (await db.execute(Message.__table__.select())).all()
    assert (await voice_session(db)).id == second.id


def test_voice_mode_follows_the_session_kind_and_is_not_user_selectable():
    assert effective_agent_mode("voice", "full_permission") is AgentMode.VOICE
    assert effective_agent_mode("voice", None) is AgentMode.VOICE
    assert effective_agent_mode("chat", "read_only") is AgentMode.READ_ONLY
    assert effective_agent_mode("chat", None) is AgentMode.NORMAL
    assert AgentMode.VOICE not in {item.id for item in list_agent_mode_definitions()}
