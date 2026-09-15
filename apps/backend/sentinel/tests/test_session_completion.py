import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Message, Session
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.service import SessionService


@pytest.mark.asyncio
async def test_final_responses_drive_completion_unread_and_preview():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    registry = AgentRunRegistry()
    service = SessionService(run_registry=registry)
    task = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as db:
            session = Session(id=uuid4(), user_id="local")
            db.add(session)
            now = datetime.now(UTC)

            def message(role="assistant", **metadata):
                item = Message(
                    session_id=session.id,
                    role=role,
                    content="Response",
                    metadata_json=metadata,
                    created_at=now,
                )
                db.add(item)
                return item

            async def preview():
                return await service.list_messages(
                    db,
                    session_id=session.id,
                    user_id="local",
                    limit=1,
                    before=None,
                    final_only=True,
                )

            # Tools, commentary (even stop-labelled), and truncated responses are not finals.
            message(stop_reason="tool_use")
            message(role="tool_result")
            message(
                stop_reason="stop", responses_output=[{"type": "message", "phase": "commentary"}]
            )
            message(stop_reason="stop", tool_calls=[{"name": "runtime"}])
            message(stop_reason="length")
            message(stop_reason="max_tokens")
            assert await service.completion_ids(db, [session]) == {}
            assert await service.compute_unread_flags(db, [session]) == {session.id: False}
            assert (await preview()).items == []

            final = message(
                stop_reason="stop",
                responses_output=[
                    {"type": "message", "phase": "commentary"},
                    {"type": "message", "phase": "final_answer"},
                ],
            )
            await db.flush()
            final_id = str(final.id)
            assert await service.completion_ids(db, [session]) == {session.id: final_id}
            assert await service.compute_unread_flags(db, [session]) == {session.id: True}
            assert [m.id for m in (await preview()).items] == [final.id]

            # Intermediate activity must not create unread results or displace the preview.
            session.last_read_at = now + timedelta(seconds=1)
            now += timedelta(seconds=2)
            for _ in range(35):
                message(stop_reason="tool_use")
                message(role="tool_result")
            assert await service.compute_unread_flags(db, [session]) == {session.id: False}
            assert await service.completion_ids(db, [session]) == {session.id: final_id}
            assert [m.id for m in (await preview()).items] == [final.id]

            # Providers without response phases still have an explicit end-turn marker.
            final2 = message(stop_reason="end_turn")
            await db.flush()
            task = await registry.start(str(session.id), asyncio.Event().wait())
            assert await service.compute_unread_flags(db, [session]) == {session.id: False}
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await registry.clear(str(session.id), task)
            assert await service.compute_unread_flags(db, [session]) == {session.id: True}
            assert await service.completion_ids(db, [session]) == {session.id: str(final2.id)}
            assert [m.id for m in (await preview()).items] == [final2.id]
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()
