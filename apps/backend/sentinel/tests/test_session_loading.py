from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Message, Session
from app.routers.sessions import _message_response
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.sessions.errors import MessageNotFoundError
from app.services.sessions.service import SessionService
from app.services.ws.ws_stream_service import (
    load_history,
    unresolved_tool_calls_from_history,
)


@pytest.mark.asyncio
async def test_pagination_is_bounded_stable_and_preserves_screenshots_and_full_history():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine)
    service = SessionService(run_registry=AgentRunRegistry())
    sid, other = uuid4(), uuid4()
    screenshot = {
        "mime_type": "image/png",
        "base64": "screenshot-bytes",
        "filename": "desktop.png",
    }
    metadata = {
        "attachments": [screenshot],
        "provider_usage": {"usage": {"attribution": "large diagnostics" * 1000}},
        "run_context": {"system_messages": ["private full prompt"]},
        "responses_output": [{"type": "reasoning", "encrypted_content": "keep-for-agent"}],
        "generation": {"provider": "test"},
        "tool_calls": [
            {
                "id": "pending-call",
                "name": "runtime",
                "arguments": {"action": "screenshot"},
            }
        ],
    }
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([Session(id=sid, user_id="local"), Session(id=other, user_id="local")])
            # All timestamps equal: paging must use a stable insertion-order tie breaker.
            now = datetime.now(UTC)
            ids = [uuid4() for _ in range(123)]
            for i, mid in enumerate(ids):
                db.add(
                    Message(
                        id=mid,
                        session_id=sid,
                        role="assistant",
                        content=f"screenshot {i}",
                        metadata_json=metadata,
                        created_at=now,
                    )
                )
            foreign = Message(
                id=uuid4(),
                session_id=other,
                role="user",
                content="other",
                created_at=now,
            )
            db.add(foreign)
            foreign_id = foreign.id
            await db.commit()
        received = []
        before = None
        while True:
            async with factory() as db:
                page = await service.list_messages(
                    db,
                    session_id=sid,
                    user_id="local",
                    limit=50,
                    before=before,
                    chat_view=True,
                )
                assert (
                    len([item for item in db.identity_map.values() if isinstance(item, Message)])
                    <= 51
                )
                for item in page.items:
                    response = _message_response(item)
                    assert response.metadata["attachments"] == [screenshot]
                    assert response.metadata["tool_calls"] == metadata["tool_calls"]
                    assert response.content.startswith("screenshot ")
                    assert "run_context" not in response.metadata
                    assert "provider_usage" not in response.metadata
                    assert "responses_output" not in response.metadata
                received.extend(item.id for item in page.items)
                if not page.has_more:
                    break
                before = page.items[-1].id
        assert received == list(reversed(ids))
        async with factory() as db:
            with pytest.raises(MessageNotFoundError):
                await service.list_messages(
                    db, session_id=sid, user_id="local", limit=50, before=foreign_id
                )
        # Fresh requests for detailed logs and agent history keep every original field.
        async with factory() as db:
            full = await service.list_messages(
                db, session_id=sid, user_id="local", limit=1, before=None
            )
            assert full.items[0].metadata_json == metadata
        async with factory() as db:
            history = await load_history(db, sid)
            assert all(item["metadata"] == metadata for item in history)
        async with factory() as db:
            reconnect = await load_history(db, sid, tool_state_only=True)
            assert unresolved_tool_calls_from_history(
                reconnect
            ) == unresolved_tool_calls_from_history(history)
            assert all(
                item["content"] == "" and "attachments" not in item["metadata"]
                for item in reconnect
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ready_workspace_only_needs_one_status_request(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.runtime import workspace_containers

    workspace = uuid4()
    statuses = AsyncMock(return_value={str(workspace): {"state": "running"}})
    start = AsyncMock()
    monkeypatch.setattr(workspace_containers, "statuses", statuses)
    monkeypatch.setattr(workspace_containers, "start", start)
    await workspace_containers.ensure_ready(workspace, "/project", [])
    statuses.assert_awaited_once()
    start.assert_not_awaited()
