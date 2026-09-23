from datetime import UTC, datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.database.engine import create_database_engine
from app.models import Base, Session, ToolApproval
from app.services.sessions.attention import pending_approval_sessions
from app.routers.sessions import _session_list_item_response


@pytest.mark.asyncio
async def test_only_live_pending_approvals_need_attention(tmp_path):
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path}/instance.sqlite")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    pending, resolved, expired, other = [uuid4() for _ in range(4)]
    now = datetime.now(UTC)
    async with factory() as db:
        db.add_all(Session(id=sid, user_id="local") for sid in (pending, resolved, expired, other))
        await db.flush()
        for sid, state, expiry in [
            (pending, "pending", 1),
            (resolved, "approved", 1),
            (expired, "pending", -1),
            (other, "pending", 1),
        ]:
            db.add(
                ToolApproval(
                    session_id=sid,
                    provider="git",
                    tool_name="git",
                    action="git.write",
                    status=state,
                    expires_at=now + timedelta(hours=expiry),
                )
            )
        await db.commit()
        assert await pending_approval_sessions(db, [pending, resolved, expired]) == {pending}
        assert await pending_approval_sessions(db, []) == set()
    await engine.dispose()


@pytest.mark.asyncio
async def test_approval_marks_running_session_as_waiting():
    session = SimpleNamespace(
        id=uuid4(),
        workspace_id=None,
        user_id="local",
        agent_id=None,
        parent_session_id=None,
        title="Approval",
        started_at=datetime.now(UTC),
    )
    service = SimpleNamespace(is_session_running=AsyncMock(return_value=True))
    waiting = await _session_list_item_response(session, service, awaiting_approval=True)
    assert waiting.is_running and waiting.awaiting_input
    cleared = await _session_list_item_response(session, service)
    assert cleared.is_running and not cleared.awaiting_input
