import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Message, Session, SessionSummary, Workspace
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.history import context_history
from app.services.sessions.service import SessionService
from app.services.sessions.usage import conversation_usage


@pytest.mark.asyncio
async def test_fork_copies_history_and_compaction_without_starting_or_sharing_runtime():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    registry = AgentRunRegistry()
    service = SessionService(run_registry=registry)
    task = asyncio.create_task(asyncio.Event().wait())
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            workspace = Workspace(
                id=uuid4(), name="shared", machine_id=uuid4(), directory="/shared"
            )
            source = Session(
                id=uuid4(), user_id="local", title="Original", workspace_id=workspace.id
            )
            db.add_all([workspace, source])
            now = datetime.now(UTC)
            ids = sorted([uuid4(), uuid4(), uuid4()], key=str)
            original = [
                Message(
                    id=ids[0], session_id=source.id, role="user", content="Task", created_at=now
                ),
                Message(
                    id=ids[1],
                    session_id=source.id,
                    role="assistant",
                    content="Done",
                    created_at=now,
                    metadata_json={
                        "provider_usage": {
                            "usage": {"input_tokens": 10, "output_tokens": 4},
                            "price_kind": "api",
                        },
                        "stop_reason": "stop",
                    },
                ),
                Message(
                    id=ids[2],
                    session_id=source.id,
                    role="user",
                    content="Follow up",
                    created_at=now,
                    metadata_json={
                        "steering": "delivered",
                        "steering_after_message_id": str(ids[1]),
                        "approval": {"pending": True},
                        "retry_settings": {"model": "old"},
                    },
                ),
            ]
            db.add_all(original)
            db.add(
                SessionSummary(
                    session_id=source.id,
                    summary={"summary_text": "Task complete", "through_message_id": str(ids[1])},
                )
            )
            await db.commit()
            await registry.register(str(source.id), task)
            registry.enqueue_interjection(str(source.id), object())

            fork = await service.fork_session(db, session_id=source.id, user_id="local")
            assert fork.workspace_id == source.workspace_id
            assert fork.parent_session_id is None
            assert fork.title == "Original (fork)"
            assert fork.latest_system_prompt is None
            assert fork.conversation_message_count == 3
            assert not await registry.is_running(str(fork.id))
            assert not registry.has_interjections(str(fork.id))
            assert await registry.is_running(str(source.id))
            rows = (
                (
                    await db.execute(
                        select(Message)
                        .where(Message.session_id == fork.id)
                        .order_by(Message.created_at, Message.id)
                    )
                )
                .scalars()
                .all()
            )
            assert [m.content for m in rows[:3]] == ["Task", "Done", "Follow up"]
            assert not set(ids) & {m.id for m in rows}
            assert rows[-1].role == "system"
            assert rows[-1].metadata_json["source"] == "session_fork"
            assert "Wait for" in rows[-1].content
            assert "approval" not in rows[2].metadata_json
            assert "retry_settings" not in rows[2].metadata_json
            assert original[2].metadata_json["approval"]["pending"]
            summary, history = await context_history(db, fork.id)
            assert summary.summary["through_message_id"] == str(rows[1].id)
            assert [m.content for m in history] == [rows[2].content, rows[3].content]
            from app.services.agent.context_builder import ContextBuilder
            from sentral.llm.generic.types import SystemMessage

            context = ContextBuilder()._convert_history_messages(history)
            assert isinstance(context[-1], SystemMessage)
            assert context[-1].content == rows[-1].content
            assert (await conversation_usage(db, fork.id))["requests"] == 0
            await db.execute(delete(Message).where(Message.session_id == source.id))
            await db.execute(delete(SessionSummary).where(SessionSummary.session_id == source.id))
            await db.execute(delete(Session).where(Session.id == source.id))
            await db.commit()
            _, history = await context_history(db, fork.id)
            assert len(history) == 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await engine.dispose()


def test_fork_endpoint_does_not_dispatch_a_turn():
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app.main import app
    from tests.fake_db import FakeDB
    from tests.helpers import install_fake_db_overrides, restore_test_app

    db = FakeDB()
    source = Session(id=uuid4(), user_id="local", title="Empty", status="active")
    db.add(source)
    old_init = install_fake_db_overrides(app_db=db)
    try:
        client = TestClient(
            app, headers={"x-sentinel-desktop-token": "test-desktop-transport-token"}
        )
        with patch("app.routers.sessions.run_agent_once") as run:
            response = client.post(f"/api/v1/instances/main/sessions/{source.id}/fork")
            assert response.status_code == 200, response.text
            assert response.json()["id"] != str(source.id)
            assert response.json()["title"] == "Empty (fork)"
            run.assert_not_called()
        assert len(db.storage[Message]) == 1
        assert db.storage[Message][0].role == "system"
        assert client.post(f"/api/v1/instances/main/sessions/{uuid4()}/fork").status_code == 404
    finally:
        restore_test_app(old_init)
