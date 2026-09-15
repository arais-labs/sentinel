import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Session, SessionBinding
from app.services.sessions import session_bindings
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def test_resolve_owner_active_session_returns_none_without_explicit_binding():
    db = FakeDB()

    resolved = _run(
        session_bindings.resolve_owner_active_session(db, user_id="admin", agent_id="dev-agent")
    )

    assert resolved is None
    assert not any(
        b.binding_type == session_bindings.OWNER_ACTIVE_BINDING_TYPE
        for b in db.storage[SessionBinding]
    )


def test_set_owner_active_session_points_owner_active():
    db = FakeDB()
    other = Session(user_id="admin", title="Project")
    db.add(other)

    switched = _run(
        session_bindings.set_owner_active_session(db, user_id="admin", session_id=other.id)
    )
    assert switched.id == other.id

    # owner_active now points at the chosen session.
    resolved = _run(
        session_bindings.resolve_owner_active_session(db, user_id="admin", agent_id="dev-agent")
    )
    assert resolved.id == other.id

    active_owner = [
        b
        for b in db.storage[SessionBinding]
        if b.binding_type == session_bindings.OWNER_ACTIVE_BINDING_TYPE and b.is_active
    ]
    assert len(active_owner) == 1
    assert active_owner[0].binding_key == session_bindings.OWNER_ACTIVE_BINDING_KEY
    assert active_owner[0].session_id == other.id
    assert active_owner[0].metadata_json == {"source": "telegram_session_switch"}


def test_set_owner_active_session_rejects_non_root_session():
    db = FakeDB()
    root = Session(user_id="admin", title="Root")
    db.add(root)
    child = Session(user_id="admin", title="Child", parent_session_id=root.id)
    db.add(child)

    with pytest.raises(session_bindings.SessionBindingTargetInvalidError):
        _run(session_bindings.set_owner_active_session(db, user_id="admin", session_id=child.id))


def test_set_owner_active_session_rejects_telegram_route_session():
    db = FakeDB()
    routed = Session(user_id="admin", title="TG DM · Owner")
    db.add(routed)
    _run(
        session_bindings.bind_session(
            db,
            user_id="admin",
            binding_type=session_bindings.TELEGRAM_DM_BINDING_TYPE,
            binding_key="dm:123:123",
            session_id=routed.id,
        )
    )

    with pytest.raises(session_bindings.SessionBindingTargetInvalidError):
        _run(session_bindings.set_owner_active_session(db, user_id="admin", session_id=routed.id))


def test_list_recent_owner_sessions_newest_first_excludes_telegram_capped():
    db = FakeDB()
    base = datetime(2026, 1, 1, tzinfo=UTC)

    # Three plain root sessions with distinct updated_at values.
    oldest = Session(user_id="admin", title="Oldest")
    oldest.created_at = base
    oldest.updated_at = base
    middle = Session(user_id="admin", title="Middle")
    middle.created_at = base + timedelta(hours=1)
    middle.updated_at = base + timedelta(hours=1)
    newest = Session(user_id="admin", title="Newest")
    newest.created_at = base + timedelta(hours=2)
    newest.updated_at = base + timedelta(hours=2)
    for session in (oldest, middle, newest):
        db.add(session)

    # A telegram-route session that must be excluded.
    routed = Session(user_id="admin", title="TG DM · Owner")
    routed.created_at = base + timedelta(hours=3)
    routed.updated_at = base + timedelta(hours=3)
    db.add(routed)
    _run(
        session_bindings.bind_session(
            db,
            user_id="admin",
            binding_type=session_bindings.TELEGRAM_DM_BINDING_TYPE,
            binding_key="dm:123:123",
            session_id=routed.id,
        )
    )

    listed = _run(session_bindings.list_recent_owner_sessions(db, user_id="admin", limit=30))

    assert [s.id for s in listed] == [newest.id, middle.id, oldest.id]
    assert routed.id not in {s.id for s in listed}

    # Capping respects the limit.
    capped = _run(session_bindings.list_recent_owner_sessions(db, user_id="admin", limit=2))
    assert [s.id for s in capped] == [newest.id, middle.id]
