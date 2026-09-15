import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.database.engine import create_database_engine
from app.models import Base, Message, Session
from app.models.triggers import Trigger
from app.models.session_bindings import SessionBinding
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.service import SessionService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "empty",
        "user",
        "image",
        "assistant",
        "prompt",
        "child",
        "binding",
        "trigger",
        "running",
        "race",
        "wrong_user",
    ],
)
async def test_close_only_discards_untouched_session(tmp_path, kind):
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path}/test.sqlite")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    registry = AgentRunRegistry()
    service = SessionService(run_registry=registry)
    task = None
    session_id = uuid4()
    try:
        async with factory() as db:
            session = Session(id=session_id, user_id="local")
            db.add(session)
            await db.commit()
            if kind in {"user", "image", "assistant"}:
                db.add(
                    Message(
                        session_id=session_id,
                        role="assistant" if kind == "assistant" else "user",
                        content="" if kind == "image" else "Hello",
                        metadata_json={"images": ["attachment"]} if kind == "image" else {},
                    )
                )
            elif kind == "prompt":
                session.initial_prompt = "Original input"
            elif kind == "child":
                db.add(Session(user_id="local", parent_session_id=session_id))
            elif kind == "binding":
                db.add(
                    SessionBinding(
                        user_id="local",
                        session_id=session_id,
                        binding_type="telegram",
                        binding_key="chat",
                    )
                )
            elif kind == "trigger":
                db.add(
                    Trigger(
                        name="Scheduled run",
                        type="cron",
                        config={},
                        action_type="agent_message",
                        action_config={"target_session_id": str(session_id)},
                    )
                )
            elif kind == "running":
                task = await registry.start(str(session_id), asyncio.sleep(60))
            await db.commit()
            cleanup = AsyncMock()
            if kind == "race":

                async def incoming(_):
                    db.add(
                        Message(session_id=session_id, role="user", content="Arrived while closing")
                    )
                    await db.flush()

                cleanup.side_effect = incoming
            deleted = await service.discard_empty_session(
                db,
                session_id=session_id,
                user_id="other" if kind == "wrong_user" else "local",
                before_delete=cleanup,
            )
            assert deleted is (kind == "empty")
            db.expire_all()
            assert (await db.get(Session, session_id) is None) is (kind == "empty")
            if kind not in {"empty", "race"}:
                cleanup.assert_not_awaited()
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
