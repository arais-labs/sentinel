import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from app.services.sessions import session_bindings


class _FakeSession:
    def add(self, _value):
        return None

    async def flush(self):
        return None

    @asynccontextmanager
    async def begin_nested(self):
        yield


def test_resolve_or_create_main_session_recovers_from_concurrent_create(monkeypatch):
    winner = SimpleNamespace(id="winner")
    calls = {"get_active": 0}

    async def fake_get_active_binding_session(*_args, **_kwargs):
        calls["get_active"] += 1
        return None if calls["get_active"] == 1 else winner

    async def fake_root_sessions(*_args, **_kwargs):
        return []

    async def fake_bind_session(*_args, **_kwargs):
        raise IntegrityError("insert session binding", {}, Exception("duplicate"))

    monkeypatch.setattr(session_bindings, "get_active_binding_session", fake_get_active_binding_session)
    monkeypatch.setattr(session_bindings, "_root_sessions", fake_root_sessions)
    monkeypatch.setattr(session_bindings, "bind_session", fake_bind_session)

    async def exercise():
        return await session_bindings.resolve_or_create_main_session(
            _FakeSession(),
            user_id="admin",
            agent_id=None,
        )

    assert asyncio.run(exercise()) is winner
