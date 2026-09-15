import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Session, SessionActionGrant, ToolApproval
from app.routers.approvals import list_session_grants, revoke_session_grant
from app.services.tools.approval import approval_waiters
from app.services.tools.approval.providers.tool import ToolApprovalProvider
from app.services.tools.approval.types import ApprovalConflictError
from app.services.tools.executor import ToolExecutionError, ToolExecutor
from app.services.tools.registry import (
    ToolApprovalEvaluation,
    ToolApprovalOutcomeStatus,
    ToolApprovalRequirement,
    ToolDefinition,
    ToolRegistry,
    ToolRuntimeContext,
)


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/instance.db")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(approval_waiters, "_POLL_INTERVAL_SECONDS", 0.005)
    yield factory
    await engine.dispose()


async def session(store, parent=None):
    async with store() as db:
        row = Session(id=uuid4(), user_id="operator", parent_session_id=parent)
        db.add(row)
        await db.commit()
        return row.id


async def pending(store, sid, action="git.write", status="pending", expires=None):
    async with store() as db:
        row = ToolApproval(
            id=uuid4(),
            session_id=sid,
            provider="git",
            tool_name="git",
            action=action,
            status=status,
            expires_at=expires or datetime.now(UTC) + timedelta(minutes=5),
        )
        db.add(row)
        await db.commit()
        return row.id


async def resolve(store, aid, scope="session", decision="approve"):
    async with store() as db:
        return await ToolApprovalProvider().resolve(
            db,
            provider="git",
            approval_id=str(aid),
            decision=decision,
            decision_by="local",
            note=None,
            scope=scope,
        )


@pytest.mark.asyncio
async def test_session_grant_covers_action_arguments_and_aliases_but_not_other_sessions_or_actions(
    store,
):
    sid = await session(store)
    aid = await pending(store, sid)
    await resolve(store, aid)
    waiter = approval_waiters.build_tool_db_approval_waiter(session_factory=store)

    async def unexpected(_):
        pytest.fail("Matching action should not ask again")

    for name, command in [("git", "git commit"), ("git_write", "git push")]:
        result = await waiter(
            name,
            {"command": command},
            ToolRuntimeContext(session_id=sid),
            ToolApprovalRequirement(action="git.write", description="Write"),
            unexpected,
        )
        assert result.status == ToolApprovalOutcomeStatus.APPROVED
        assert result.approval["approval_scope"] == "session"
        assert result.approval["session_id"] == str(sid)
        async with store() as db:
            row = await db.get(ToolApproval, UUID(result.approval["approval_id"]))
            assert row.decision_by == "local"
            assert row.payload_json["session_grant_id"]
    asked = []

    async def deny(request):
        asked.append(request)
        async with store() as db:
            await ToolApprovalProvider().resolve(
                db,
                provider=request["provider"],
                approval_id=request["approval_id"],
                decision="reject",
                decision_by="local",
                note=None,
            )

    # Child/fork conversations have a parent but no inherited grant.
    for other_sid, action in [
        (sid, "git.delete"),
        (await session(store), "git.write"),
        (await session(store, sid), "git.write"),
        (None, "git.write"),
    ]:
        result = await waiter(
            "git",
            {},
            ToolRuntimeContext(session_id=other_sid),
            ToolApprovalRequirement(action=action, description="Write"),
            deny,
        )
        assert result.status == ToolApprovalOutcomeStatus.REJECTED
    assert len(asked) == 4


@pytest.mark.asyncio
async def test_grant_releases_only_live_matching_requests_and_revocation_restores_prompt(
    store,
):
    sid, other = await session(store), await session(store)
    first = await pending(store, sid)
    same = await pending(store, sid)
    different = await pending(store, sid, "git.delete")
    other_request = await pending(store, other)
    expired = await pending(store, sid, expires=datetime.now(UTC) - timedelta(seconds=1))
    cancelled = await pending(store, sid, status="cancelled")
    await resolve(store, first)
    async with store() as db:
        for aid in [first, same]:
            row = await db.get(ToolApproval, aid)
            assert row.status == "approved"
            assert row.payload_json["approval_scope"] == "session"
        for aid in [different, other_request, expired]:
            assert (await db.get(ToolApproval, aid)).status == "pending"
        assert (await db.get(ToolApproval, cancelled)).status == "cancelled"
        grants = await list_session_grants(sid, db)
        assert len(grants) == 1
        await revoke_session_grant(other, grants[0].id, db)
        assert len(await list_session_grants(sid, db)) == 1
        await revoke_session_grant(sid, grants[0].id, db)
        assert await list_session_grants(sid, db) == []
    calls = []

    async def approve_once(request):
        calls.append(request)
        await resolve(store, UUID(request["approval_id"]), scope="once")

    waiter = approval_waiters.build_tool_db_approval_waiter(session_factory=store)
    for _ in range(2):
        await waiter(
            "git",
            {},
            ToolRuntimeContext(session_id=sid),
            ToolApprovalRequirement(action="git.write", description="Write"),
            approve_once,
        )
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expired", "cancelled", "rejected", "approved", "no_session"])
async def test_invalid_approval_cannot_create_session_grant(store, kind):
    sid = None if kind == "no_session" else await session(store)
    aid = await pending(
        store,
        sid,
        status=kind if kind in {"cancelled", "rejected", "approved"} else "pending",
        expires=datetime.now(UTC) - timedelta(seconds=1) if kind == "expired" else None,
    )
    with pytest.raises(ApprovalConflictError):
        await resolve(store, aid)
    async with store() as db:
        assert (await db.scalars(select(SessionActionGrant))).all() == []


@pytest.mark.asyncio
async def test_concurrent_resolution_and_request_creation_have_one_grant_and_no_stranded_waiter(
    store,
):
    sid = await session(store)
    aid = await pending(store, sid)
    waiter = approval_waiters.build_tool_db_approval_waiter(session_factory=store)
    results = await asyncio.gather(
        resolve(store, aid),
        resolve(store, aid),
        asyncio.wait_for(
            waiter(
                "git",
                {},
                ToolRuntimeContext(session_id=sid),
                ToolApprovalRequirement(action="git.write", description="Write"),
            ),
            timeout=2,
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ApprovalConflictError) for result in results[:2]) == 1
    assert results[2].status == ToolApprovalOutcomeStatus.APPROVED
    async with store() as db:
        assert len((await db.scalars(select(SessionActionGrant))).all()) == 1
        assert all(
            row.status == "approved" for row in (await db.scalars(select(ToolApproval))).all()
        )


@pytest.mark.asyncio
async def test_expiry_and_cancellation_cannot_overwrite_approval(store):
    sid = await session(store)
    aid = await pending(store, sid)
    await resolve(store, aid)
    await approval_waiters._cancel_pending_approval(
        session_factory=store, approval_id=aid, note="Cancelled"
    )
    result = await approval_waiters._wait_for_resolution(
        session_factory=store, approval_id=aid, timeout_seconds=0
    )
    assert result.status == ToolApprovalOutcomeStatus.APPROVED


@pytest.mark.asyncio
async def test_explicit_deny_wins_even_with_grant(store):
    sid = await session(store)
    await resolve(store, await pending(store, sid))
    executed = []

    async def execute(payload, runtime):
        executed.append(payload)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="git",
            description="Git",
            parameters_schema={},
            execute=execute,
            approval_check=lambda: ToolApprovalEvaluation.deny("Denied by module policy"),
        )
    )
    executor = ToolExecutor(
        registry,
        approval_waiter=approval_waiters.build_tool_db_approval_waiter(session_factory=store),
    )
    with pytest.raises(ToolExecutionError, match="Denied by module policy"):
        await executor.execute("git", {}, runtime=ToolRuntimeContext(session_id=sid))
    assert not executed


@pytest.mark.asyncio
async def test_grants_do_not_cross_instance_databases_and_are_deleted_with_conversation(
    store, tmp_path
):
    sid = await session(store)
    await resolve(store, await pending(store, sid))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/other-instance.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        other_store = async_sessionmaker(engine, expire_on_commit=False)
        async with other_store() as db:
            db.add(Session(id=sid, user_id="operator"))
            await db.commit()
            assert await list_session_grants(sid, db) == []
        async with store() as db:
            assert len(await list_session_grants(sid, db)) == 1
            await db.delete(await db.get(Session, sid))
            await db.commit()
            assert await list_session_grants(sid, db) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_desktop_api_derives_scope_from_approval_and_validates_decisions(store):
    import httpx
    from fastapi import FastAPI

    from app.dependencies import get_db
    from app.middleware.desktop import DesktopTransportMiddleware
    from app.routers.approvals import router
    from app.services.tools.approval import ApprovalService

    app = FastAPI()
    app.state.approval_service = ApprovalService()
    app.add_middleware(DesktopTransportMiddleware, token="operator-only")
    app.include_router(router, prefix="/api/v1/instances/{instance_name}/approvals")

    async def database():
        async with store() as db:
            yield db

    app.dependency_overrides[get_db] = database
    sid = await session(store)
    aid = await pending(store, sid)
    base = "/api/v1/instances/test/approvals"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post(f"{base}/git/{aid}/approve", json={"scope": "session"})
        ).status_code == 403
        client.headers["x-sentinel-desktop-token"] = "operator-only"
        assert (
            await client.post(f"{base}/git/{aid}/approve", json={"scope": "global"})
        ).status_code == 422
        assert (
            await client.post(f"{base}/git/{aid}/reject", json={"scope": "session"})
        ).status_code == 409
        response = await client.post(
            f"{base}/git/{aid}/approve",
            json={
                "scope": "session",
                "session_id": str(uuid4()),
                "action": "*",
            },
        )
        assert response.status_code == 200
        assert response.json()["metadata"]["approval_scope"] == "session"
        grants = (await client.get(f"{base}/sessions/{sid}/grants")).json()
        assert [grant["action"] for grant in grants] == ["git.write"]
        assert grants[0]["session_id"] == str(sid)
        assert (
            await client.delete(f"{base}/sessions/{sid}/grants/{grants[0]['id']}")
        ).status_code == 204
        assert (await client.get(f"{base}/sessions/{sid}/grants")).json() == []
