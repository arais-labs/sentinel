from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.models import Session
from app.models.system import SystemSetting
from app.services.telegram_bridge import (
    TELEGRAM_CHAT_ROUTES_KEY,
    TelegramBridge,
    send_telegram_message_tool,
    start_telegram_bridge,
    telegram_manage_integration_tool,
)
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


def _build_bridge(*, db: FakeDB, user_id: str) -> TelegramBridge:
    class _DBFactory:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, tb):
            return None

    return TelegramBridge(
        bot_token="dummy",
        user_id=user_id,
        agent_loop=None,
        run_registry=object(),
        ws_manager=object(),
        db_factory=lambda: _DBFactory(),
    )


def test_resolve_default_session_uses_newest_active_root_when_no_target():
    db = FakeDB()
    now = datetime.now(UTC)
    old_target = settings.telegram_target_session_id
    settings.telegram_target_session_id = None

    try:
        other_user_session = Session(user_id="dev-admin", title="Other", status="active")
        other_user_session.created_at = now - timedelta(minutes=20)
        db.add(other_user_session)

        owner_old = Session(user_id="admin", title="Main", status="active")
        owner_old.created_at = now - timedelta(minutes=10)
        db.add(owner_old)

        owner_new = Session(user_id="admin", title="Main 2", status="active")
        owner_new.created_at = now - timedelta(minutes=1)
        db.add(owner_new)

        bridge = _build_bridge(db=db, user_id="admin")
        resolved = _run(bridge._resolve_default_session())  # noqa: SLF001
        assert resolved == owner_new.id
    finally:
        settings.telegram_target_session_id = old_target


def test_resolve_default_session_prefers_explicit_target_when_valid():
    db = FakeDB()
    now = datetime.now(UTC)

    owner_old = Session(user_id="admin", title="Main", status="active")
    owner_old.created_at = now - timedelta(minutes=10)
    db.add(owner_old)

    owner_new = Session(user_id="admin", title="Main 2", status="active")
    owner_new.created_at = now - timedelta(minutes=1)
    db.add(owner_new)

    old_target = settings.telegram_target_session_id
    settings.telegram_target_session_id = str(owner_old.id)
    try:
        bridge = _build_bridge(db=db, user_id="admin")
        resolved = _run(bridge._resolve_default_session())  # noqa: SLF001
        assert resolved == owner_old.id
    finally:
        settings.telegram_target_session_id = old_target


def test_resolve_default_session_reactivates_explicit_target_when_ended():
    db = FakeDB()
    now = datetime.now(UTC)

    owner_target = Session(user_id="admin", title="Main Ended", status="ended")
    owner_target.created_at = now - timedelta(minutes=2)
    db.add(owner_target)

    owner_other = Session(user_id="admin", title="Other Active", status="active")
    owner_other.created_at = now - timedelta(minutes=1)
    db.add(owner_other)

    old_target = settings.telegram_target_session_id
    settings.telegram_target_session_id = str(owner_target.id)
    try:
        bridge = _build_bridge(db=db, user_id="admin")
        resolved = _run(bridge._resolve_default_session())  # noqa: SLF001
        assert resolved == owner_target.id
        assert owner_target.status == "active"
    finally:
        settings.telegram_target_session_id = old_target


def test_resolve_default_session_uses_newest_root_when_no_active_root():
    db = FakeDB()
    now = datetime.now(UTC)
    old_target = settings.telegram_target_session_id
    settings.telegram_target_session_id = None

    try:
        owner_old = Session(user_id="admin", title="Old", status="ended")
        owner_old.created_at = now - timedelta(minutes=10)
        db.add(owner_old)

        owner_new = Session(user_id="admin", title="Newest", status="ended")
        owner_new.created_at = now - timedelta(minutes=1)
        db.add(owner_new)

        bridge = _build_bridge(db=db, user_id="admin")
        resolved = _run(bridge._resolve_default_session())  # noqa: SLF001
        assert resolved == owner_new.id
        assert owner_new.status == "active"
    finally:
        settings.telegram_target_session_id = old_target


def test_resolve_default_session_creates_main_when_missing():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")

    resolved = _run(bridge._resolve_default_session())  # noqa: SLF001
    assert resolved is not None

    sessions = db.storage[Session]
    assert len(sessions) == 1
    assert sessions[0].user_id == "admin"
    assert sessions[0].title == "Main"
    assert sessions[0].status == "active"


def test_telegram_manage_tool_configure_sets_owner_and_target():
    app_state = type(
        "State", (), {"telegram_bridge": None, "telegram_stop_event": None, "telegram_task": None}
    )()
    tool = telegram_manage_integration_tool(app_state)

    old_token = settings.telegram_bot_token
    old_owner = settings.telegram_owner_user_id
    old_target = settings.telegram_target_session_id
    old_dev_user = settings.dev_user_id
    settings.dev_user_id = "dev-admin"
    settings.telegram_bot_token = None
    settings.telegram_owner_user_id = None
    settings.telegram_target_session_id = None

    try:
        with (
            patch(
                "app.services.telegram_bridge.resolve_owner_user_id_from_session",
                new=AsyncMock(return_value="admin"),
            ),
            patch(
                "app.services.telegram_bridge.persist_telegram_settings",
                new=AsyncMock(return_value=None),
            ) as persist_mock,
            patch(
                "app.services.telegram_bridge.start_telegram_bridge",
                new=AsyncMock(return_value=True),
            ) as start_mock,
        ):
            result = _run(
                tool.execute(
                    {
                        "action": "configure",
                        "bot_token": "12345:abcde",
                        "owner_session_id": "317dc122-62fd-481e-ba03-907ec45a7c5a",
                    }
                )
            )

        assert result["success"] is True
        assert settings.telegram_owner_user_id == "admin"
        assert settings.telegram_target_session_id == "317dc122-62fd-481e-ba03-907ec45a7c5a"
        persist_mock.assert_awaited_once()
        start_mock.assert_awaited_once()
    finally:
        settings.telegram_bot_token = old_token
        settings.telegram_owner_user_id = old_owner
        settings.telegram_target_session_id = old_target
        settings.dev_user_id = old_dev_user


def test_send_telegram_message_tool_refuses_owner_chat_by_default():
    class _Bridge:
        is_running = True
        connected_chats = {12345: {"chat_type": "private", "title": "Owner"}}

        @property
        def bot_username(self):
            return "sentinel_bot"

        async def send_message(self, chat_id: int, text: str) -> bool:
            return True

    app_state = type("State", (), {"telegram_bridge": _Bridge()})()
    tool = send_telegram_message_tool(app_state)

    old_owner_chat = settings.telegram_owner_chat_id
    settings.telegram_owner_chat_id = "12345"
    try:
        result = _run(tool.execute({"chat_id": 12345, "message": "hello"}))
        assert result["success"] is False
        assert "owner Telegram DM" in str(result["error"])
    finally:
        settings.telegram_owner_chat_id = old_owner_chat


def test_start_telegram_bridge_resolves_owner_from_target_session():
    app_state = type(
        "State",
        (),
        {
            "ws_manager": object(),
            "agent_run_registry": object(),
            "agent_loop": object(),
            "telegram_bridge": None,
            "telegram_stop_event": None,
            "telegram_task": None,
        },
    )()

    old_token = settings.telegram_bot_token
    old_owner = settings.telegram_owner_user_id
    old_target = settings.telegram_target_session_id

    settings.telegram_bot_token = "12345:abcde"
    settings.telegram_owner_user_id = None
    settings.telegram_target_session_id = "317dc122-62fd-481e-ba03-907ec45a7c5a"
    try:
        with (
            patch(
                "app.services.telegram_bridge.resolve_owner_user_id_from_session",
                new=AsyncMock(return_value="admin"),
            ),
            patch(
                "app.services.telegram_bridge.reconcile_telegram_target_session",
                new=AsyncMock(return_value="317dc122-62fd-481e-ba03-907ec45a7c5a"),
            ),
            patch(
                "app.services.telegram_bridge._upsert_setting",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.telegram_bridge.TelegramBridge.start",
                new=AsyncMock(return_value=None),
            ),
        ):
            started = _run(start_telegram_bridge(app_state))
        assert started is True
        assert settings.telegram_owner_user_id == "admin"
        assert app_state.telegram_bridge is not None
    finally:
        settings.telegram_bot_token = old_token
        settings.telegram_owner_user_id = old_owner
        settings.telegram_target_session_id = old_target


def test_handle_ask_queues_override_text():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    update = SimpleNamespace(
        message=SimpleNamespace(text="/ask hello world", caption=None, reply_text=AsyncMock()),
        effective_chat=SimpleNamespace(id=123, type="group", title="Ops"),
        effective_user=SimpleNamespace(
            id=42, full_name="John Smith", first_name="John", username="john"
        ),
    )
    context = SimpleNamespace(args=["hello", "world"])

    async def _exercise() -> dict:
        await bridge._handle_ask(update, context)  # noqa: SLF001
        _queued_update, metadata = await bridge._queue.get()  # noqa: SLF001
        return metadata

    metadata = _run(_exercise())
    assert metadata["telegram_text_override"] == "hello world"
    assert metadata["telegram_chat_type"] == "group"


def test_handle_ask_without_args_shows_usage_and_does_not_queue():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    reply_text = AsyncMock()
    update = SimpleNamespace(
        message=SimpleNamespace(text="/ask", caption=None, reply_text=reply_text),
        effective_chat=SimpleNamespace(id=123, type="group", title="Ops"),
        effective_user=SimpleNamespace(
            id=42, full_name="John Smith", first_name="John", username="john"
        ),
    )
    context = SimpleNamespace(args=[])

    _run(bridge._handle_ask(update, context))  # noqa: SLF001
    reply_text.assert_awaited_once()
    assert bridge._queue.empty()  # noqa: SLF001


def test_enqueue_marks_owner_by_owner_chat_id_fallback():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    old_owner_chat = settings.telegram_owner_chat_id
    old_owner_tg_user = settings.telegram_owner_telegram_user_id
    settings.telegram_owner_chat_id = "123"
    settings.telegram_owner_telegram_user_id = None
    try:
        update = SimpleNamespace(
            message=SimpleNamespace(text="hello", caption=None, reply_text=AsyncMock()),
            effective_chat=SimpleNamespace(id=123, type="private", title=None, full_name="Owner"),
            effective_user=SimpleNamespace(
                id=999, full_name="John Smith", first_name="John", username="john"
            ),
        )
        context = SimpleNamespace(args=[])

        async def _exercise() -> dict:
            await bridge._handle_message(update, context)  # noqa: SLF001
            _queued_update, metadata = await bridge._queue.get()  # noqa: SLF001
            return metadata

        metadata = _run(_exercise())
        assert metadata["telegram_is_owner"] is True
    finally:
        settings.telegram_owner_chat_id = old_owner_chat
        settings.telegram_owner_telegram_user_id = old_owner_tg_user


def test_should_reply_inline_owner_private_only():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    private_chat = SimpleNamespace(id=123, type="private")
    group_chat = SimpleNamespace(id=-1001, type="group")
    assert (
        bridge._should_reply_inline(private_chat, {"telegram_is_owner": True}) is True
    )  # noqa: SLF001
    assert (
        bridge._should_reply_inline(private_chat, {"telegram_is_owner": False}) is False
    )  # noqa: SLF001
    assert (
        bridge._should_reply_inline(group_chat, {"telegram_is_owner": True}) is False
    )  # noqa: SLF001


def test_resolve_inbound_session_owner_dm_uses_owner_main_route():
    db = FakeDB()
    now = datetime.now(UTC)
    owner_main = Session(user_id="admin", title="Main", status="active")
    owner_main.created_at = now
    db.add(owner_main)

    old_target = settings.telegram_target_session_id
    settings.telegram_target_session_id = str(owner_main.id)
    try:
        bridge = _build_bridge(db=db, user_id="admin")
        private_chat = SimpleNamespace(id=123, type="private", title=None)
        owner_user = SimpleNamespace(id=123, full_name="Owner", first_name="Owner")
        session_id, scope = _run(
            bridge._resolve_inbound_session(  # noqa: SLF001
                db,
                chat=private_chat,
                user=owner_user,
                metadata={"telegram_is_owner": True},
            )
        )
        assert session_id == owner_main.id
        assert scope == "owner_main"
    finally:
        settings.telegram_target_session_id = old_target


def test_resolve_inbound_session_group_routes_to_persistent_channel():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    group_chat = SimpleNamespace(id=-100123, type="supergroup", title="Ops")
    user_a = SimpleNamespace(id=111, full_name="John Smith", first_name="John")
    user_b = SimpleNamespace(id=222, full_name="Ron Cahlon", first_name="Ron")

    first_session_id, first_scope = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=group_chat,
            user=user_a,
            metadata={"telegram_is_owner": False},
        )
    )
    second_session_id, second_scope = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=group_chat,
            user=user_b,
            metadata={"telegram_is_owner": False},
        )
    )

    assert first_scope == "group_channel"
    assert second_scope == "group_channel"
    assert first_session_id == second_session_id

    routes_setting = db.storage[SystemSetting][0]
    payload = json.loads(routes_setting.value)
    assert "group:-100123" in payload
    assert payload["group:-100123"]["session_id"] == str(first_session_id)
    sessions = db.storage[Session]
    assert any((s.title or "").startswith("TG Group · Ops") for s in sessions)


def test_resolve_inbound_session_non_owner_dm_has_private_channel_per_user():
    db = FakeDB()
    bridge = _build_bridge(db=db, user_id="admin")
    chat_ron = SimpleNamespace(id=555, type="private", title=None)
    ron = SimpleNamespace(id=555, full_name="Ron Cahlon", first_name="Ron")
    chat_john = SimpleNamespace(id=777, type="private", title=None)
    john = SimpleNamespace(id=777, full_name="John Smith", first_name="John")

    ron_session_1, ron_scope_1 = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=chat_ron,
            user=ron,
            metadata={"telegram_is_owner": False},
        )
    )
    ron_session_2, ron_scope_2 = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=chat_ron,
            user=ron,
            metadata={"telegram_is_owner": False},
        )
    )
    john_session, john_scope = _run(
        bridge._resolve_inbound_session(  # noqa: SLF001
            db,
            chat=chat_john,
            user=john,
            metadata={"telegram_is_owner": False},
        )
    )

    assert ron_scope_1 == "dm_channel"
    assert ron_scope_2 == "dm_channel"
    assert john_scope == "dm_channel"
    assert ron_session_1 == ron_session_2
    assert john_session != ron_session_1

    routes_settings = [s for s in db.storage[SystemSetting] if s.key == TELEGRAM_CHAT_ROUTES_KEY]
    assert routes_settings
    payload = json.loads(routes_settings[0].value)
    assert "dm:555:555" in payload
    assert "dm:777:777" in payload
