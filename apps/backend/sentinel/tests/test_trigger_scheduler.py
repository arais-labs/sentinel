from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.models import Session, SessionBinding, Trigger, TriggerLog
from app.services.agent import ToolAdapter
from app.services.agent.sentinel_runner import PreparedRuntimeTurnContext
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.types import AgentEvent, TextContent
from app.services.sessions.agent_run_registry import AgentRunRegistry
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry
from app.services.triggers.trigger_scheduler import TriggerScheduler, compute_next_fire_at
from tests.fake_db import FakeDB


def _run(coro):
    return asyncio.run(coro)


class _SessionCtx:
    def __init__(self, db: FakeDB):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SessionFactory:
    def __init__(self, db: FakeDB):
        self._db = db

    def __call__(self):
        return _SessionCtx(self._db)


class _AgentLoopStub:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._estop = SimpleNamespace(check_level=self._check_level)
        self.context_builder = SimpleNamespace(build=self._build_context)
        registry = ToolRegistry()
        self.tool_adapter = ToolAdapter(registry, ToolExecutor(registry))
        self.provider = _StreamingProvider()

    async def estop_level(self, db):
        return await self._estop.check_level(db)

    async def prepare_runtime_turn_context(
        self,
        db,
        session_id,
        *,
        system_prompt,
        pending_user_message,
        agent_mode,
        model,
        temperature,
        max_iterations,
        stream,
    ):
        messages = await self.context_builder.build(db, session_id, system_prompt, pending_user_message, agent_mode)
        return PreparedRuntimeTurnContext(
            messages=messages,
            tools=self.tool_adapter.get_tool_schemas(),
            effective_system_prompt=system_prompt,
            runtime_context_snapshot=None,
        )

    async def persist_created_messages(self, db, session_id, created, assistant_iterations, **kwargs):
        await self._persist_messages(db, session_id, created, assistant_iterations, **kwargs)

    async def _check_level(self, _db):
        return None

    async def _build_context(self, db, session_id, system_prompt, pending_user_message, agent_mode):
        self.calls.append(
            {
                "db": db,
                "session_id": session_id,
                "user_message": pending_user_message,
                "kwargs": {
                    "system_prompt": system_prompt,
                    "agent_mode": agent_mode,
                },
            }
        )
        return []

    async def _persist_messages(self, db, session_id, created, assistant_iterations, **kwargs):
        _ = (db, session_id, created, assistant_iterations, kwargs)

    @staticmethod
    def _extract_final_text(_messages) -> str:
        return "ok"

    @staticmethod
    def _collect_attachments(_messages) -> list[dict]:
        return []

    def extract_final_text(self, messages) -> str:
        return self._extract_final_text(messages)

    def collect_attachments(self, messages) -> list[dict]:
        return self._collect_attachments(messages)


class _BlockingAgentLoopStub:
    def __init__(self) -> None:
        self._estop = SimpleNamespace(check_level=self._check_level)
        self.context_builder = SimpleNamespace(build=self._build_context)
        registry = ToolRegistry()
        self.tool_adapter = ToolAdapter(registry, ToolExecutor(registry))
        self.provider = _BlockingProvider()

    async def estop_level(self, db):
        return await self._estop.check_level(db)

    async def prepare_runtime_turn_context(
        self,
        db,
        session_id,
        *,
        system_prompt,
        pending_user_message,
        agent_mode,
        model,
        temperature,
        max_iterations,
        stream,
    ):
        messages = await self.context_builder.build(db, session_id, system_prompt, pending_user_message, agent_mode)
        return PreparedRuntimeTurnContext(
            messages=messages,
            tools=self.tool_adapter.get_tool_schemas(),
            effective_system_prompt=system_prompt,
            runtime_context_snapshot=None,
        )

    async def persist_created_messages(self, db, session_id, created, assistant_iterations, **kwargs):
        await self._persist_messages(db, session_id, created, assistant_iterations, **kwargs)

    async def _check_level(self, _db):
        return None

    async def _build_context(self, _db, _session_id, system_prompt, pending_user_message, agent_mode):
        _ = (system_prompt, pending_user_message, agent_mode)
        return []

    async def _persist_messages(self, db, session_id, created, assistant_iterations, **kwargs):
        _ = (db, session_id, created, assistant_iterations, kwargs)

    @staticmethod
    def _extract_final_text(_messages) -> str:
        return ""

    @staticmethod
    def _collect_attachments(_messages) -> list[dict]:
        return []

    def extract_final_text(self, messages) -> str:
        return self._extract_final_text(messages)

    def collect_attachments(self, messages) -> list[dict]:
        return self._collect_attachments(messages)


class _StreamingProvider(LLMProvider):
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return "fake"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "tools": list(tools or []),
                "temperature": temperature,
                "stream": False,
            }
        )
        raise AssertionError("Trigger runtime tests expect the streaming provider path")

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "tools": list(tools or []),
                "temperature": temperature,
                "stream": True,
            }
        )
        yield AgentEvent(type="text_delta", delta="ok")
        yield AgentEvent(type="done", stop_reason="stop")


class _BlockingProvider(LLMProvider):
    @property
    def name(self) -> str:
        return "fake"

    async def chat(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        _ = (messages, model, tools, temperature, reasoning_config, tool_choice)
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def stream(self, messages, model, tools=None, temperature=0.7, reasoning_config=None, tool_choice=None):
        _ = (messages, model, tools, temperature, reasoning_config, tool_choice)
        await asyncio.sleep(3600)
        if False:
            yield


class _ToolExecutorStub:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, name: str, payload: dict, **kwargs):
        self.calls.append({"name": name, "payload": payload})
        return {"ok": True}, 8


class _WSManagerStub:
    def __init__(self) -> None:
        self.message_acks: list[dict] = []
        self.thinking_events: list[str] = []
        self.agent_events: list[dict] = []

    async def broadcast_message_ack(self, session_key, message_id, content, created_at, metadata=None):
        self.message_acks.append(
            {
                "session_key": session_key,
                "message_id": message_id,
                "content": content,
                "created_at": created_at,
                "metadata": metadata or {},
            }
        )

    async def broadcast_agent_thinking(self, session_key):
        self.thinking_events.append(session_key)

    async def broadcast_agent_event(self, session_key, event):
        self.agent_events.append({"session_key": session_key, "event": event})


def test_compute_next_fire_at_for_cron_and_heartbeat():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cron_next = compute_next_fire_at("cron", {"expr": "*/5 * * * *"}, reference_time=now)
    heartbeat_next = compute_next_fire_at("heartbeat", {"interval_seconds": 30}, reference_time=now)

    assert cron_next is not None and cron_next > now
    assert heartbeat_next is not None and int((heartbeat_next - now).total_seconds()) == 30


def test_scheduler_agent_message_action_calls_agent_loop():
    db = FakeDB()
    trigger = Trigger(
        name="agent-job",
        user_id="user-1",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 60},
        action_type="agent_message",
        action_config={"message": "ping"},
        next_fire_at=datetime.now(UTC),
    )
    db.add(trigger)

    agent = _AgentLoopStub()
    scheduler = TriggerScheduler(
        agent_loop=agent,
        tool_executor=None,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )
    _run(scheduler._fire_trigger(trigger.id))

    assert agent.calls and agent.calls[0]["user_message"] == "ping"
    assert agent.provider.calls
    provider_messages = agent.provider.calls[0]["messages"]
    user_metadata = provider_messages[-1].metadata
    assert isinstance(user_metadata, dict)
    assert user_metadata.get("source") == "trigger"
    assert user_metadata.get("trigger_name") == "agent-job"
    assert user_metadata.get("trigger_id") == str(trigger.id)
    assert trigger.fire_count == 1
    assert trigger.consecutive_errors == 0
    logs = db.storage[TriggerLog]
    assert len(logs) == 1
    assert logs[0].status == "fired"


def test_scheduler_agent_message_ack_uses_plain_content_and_trigger_metadata():
    db = FakeDB()
    session = Session(user_id="user-1", title="Main", status="active")
    db.add(session)
    trigger = Trigger(
        name="heartbeat-job",
        user_id="user-1",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 60},
        action_type="agent_message",
        action_config={"message": "   ping from trigger   ", "session_id": str(session.id)},
        next_fire_at=datetime.now(UTC),
    )
    db.add(trigger)

    agent = _AgentLoopStub()
    ws = _WSManagerStub()
    scheduler = TriggerScheduler(
        agent_loop=agent,
        tool_executor=None,
        ws_manager=ws,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )
    _run(scheduler._fire_trigger(trigger.id))

    assert ws.message_acks
    ack = ws.message_acks[0]
    assert ack["content"] == "ping from trigger"
    assert ack["metadata"].get("source") == "trigger"
    assert ack["metadata"].get("trigger_name") == "heartbeat-job"


def test_scheduler_agent_loop_can_be_updated_after_init():
    db = FakeDB()
    trigger = Trigger(
        name="agent-job",
        user_id="user-1",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 60},
        action_type="agent_message",
        action_config={"message": "ping"},
        next_fire_at=datetime.now(UTC),
    )
    db.add(trigger)

    scheduler = TriggerScheduler(
        agent_loop=None,
        tool_executor=None,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )
    agent = _AgentLoopStub()
    scheduler.set_agent_loop(agent)
    _run(scheduler._fire_trigger(trigger.id))

    assert agent.calls and agent.calls[0]["user_message"] == "ping"
    assert agent.provider.calls
    assert trigger.fire_count == 1
    assert trigger.last_error is None


def test_scheduler_fire_now_records_signal_payload():
    db = FakeDB()
    trigger = Trigger(
        name="tool-job",
        type="heartbeat",
        enabled=False,
        config={"interval_seconds": 60},
        action_type="tool_call",
        action_config={"tool_name": "file_read", "payload": {"path": "/tmp/x"}},
        next_fire_at=None,
    )
    db.add(trigger)

    tools = _ToolExecutorStub()
    scheduler = TriggerScheduler(
        agent_loop=None,
        tool_executor=tools,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )
    signal = {"source": "manual", "signal": "force_invocation"}
    outcome = _run(scheduler.fire_now(db, trigger_id=trigger.id, input_payload=signal, force=True))

    assert outcome is not None
    assert outcome.log.status == "fired"
    assert outcome.log.input_payload == signal
    assert tools.calls
    assert trigger.fire_count == 1


def test_scheduler_disables_ownerless_agent_trigger_without_session_context():
    db = FakeDB()
    trigger = Trigger(
        name="dangling-agent-job",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 60},
        action_type="agent_message",
        action_config={"message": "ping"},
        next_fire_at=datetime.now(UTC),
    )
    db.add(trigger)

    scheduler = TriggerScheduler(
        agent_loop=_AgentLoopStub(),
        tool_executor=None,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )
    _run(scheduler._fire_trigger(trigger.id))

    assert trigger.enabled is False
    assert trigger.next_fire_at is None
    assert trigger.last_error is not None
    assert "no owner user_id" in trigger.last_error


def test_scheduler_routes_agent_message_to_specific_root_session():
    db = FakeDB()
    main = Session(user_id="user-1", title="Main", status="active")
    random = Session(user_id="user-1", title="Project", status="active")
    db.add(main)
    db.add(random)
    trigger = Trigger(
        name="agent-job",
        user_id="user-1",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 60},
        action_type="agent_message",
        action_config={"message": "ping", "session_id": str(random.id)},
        next_fire_at=datetime.now(UTC),
    )
    db.add(trigger)

    agent = _AgentLoopStub()
    scheduler = TriggerScheduler(
        agent_loop=agent,
        tool_executor=None,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )
    _run(scheduler._fire_trigger(trigger.id))

    assert agent.calls
    assert agent.calls[0]["session_id"] == random.id
    assert trigger.action_config.get("session_id") == str(random.id)
    assert trigger.action_config.get("route_mode") == "session"
    assert trigger.action_config.get("target_session_id") == str(random.id)


def test_scheduler_falls_back_to_main_when_target_session_missing():
    db = FakeDB()
    main = Session(user_id="user-1", title="Main", status="active")
    db.add(main)
    trigger = Trigger(
        name="agent-job",
        user_id="user-1",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 60},
        action_type="agent_message",
        action_config={
            "message": "ping",
            "route_mode": "session",
            "target_session_id": "0fb4e3d8-4238-4fae-a5df-7bf1a615b38b",
        },
        next_fire_at=datetime.now(UTC),
    )
    db.add(trigger)

    agent = _AgentLoopStub()
    scheduler = TriggerScheduler(
        agent_loop=agent,
        tool_executor=None,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )
    _run(scheduler._fire_trigger(trigger.id))

    assert agent.calls
    assert agent.calls[0]["session_id"] == main.id
    assert trigger.action_config.get("session_id") == str(main.id)
    assert trigger.action_config.get("route_mode") == "main"
    assert trigger.action_config.get("target_session_id") is None
    assert trigger.action_config.get("route_fallback_reason") == "invalid_or_deleted_target_session"


def test_scheduler_allows_telegram_route_session_target():
    db = FakeDB()
    main = Session(user_id="user-1", title="Main", status="active")
    telegram_session = Session(user_id="user-1", title="TG Group · Ops", status="active")
    db.add(main)
    db.add(telegram_session)
    db.add(
        SessionBinding(
            user_id="user-1",
            binding_type="telegram_group",
            binding_key="group:123",
            session_id=telegram_session.id,
            is_active=True,
            metadata_json={},
        )
    )
    trigger = Trigger(
        name="agent-job",
        user_id="user-1",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 60},
        action_type="agent_message",
        action_config={"message": "ping", "session_id": str(telegram_session.id)},
        next_fire_at=datetime.now(UTC),
    )
    db.add(trigger)

    agent = _AgentLoopStub()
    scheduler = TriggerScheduler(
        agent_loop=agent,
        tool_executor=None,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )
    _run(scheduler._fire_trigger(trigger.id))

    assert agent.calls
    assert agent.calls[0]["session_id"] == telegram_session.id
    assert trigger.action_config.get("session_id") == str(telegram_session.id)


def test_scheduler_tool_call_action_uses_tool_executor():
    db = FakeDB()
    trigger = Trigger(
        name="tool-job",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 60},
        action_type="tool_call",
        action_config={"tool_name": "file_read", "payload": {"path": "/tmp/x"}},
        next_fire_at=datetime.now(UTC),
    )
    db.add(trigger)

    tools = _ToolExecutorStub()
    scheduler = TriggerScheduler(
        agent_loop=None,
        tool_executor=tools,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )
    _run(scheduler._fire_trigger(trigger.id))

    assert tools.calls
    assert tools.calls[0]["name"] == "file_read"
    assert trigger.fire_count == 1


def test_scheduler_http_request_action_executes_outbound_call():
    from app.services.triggers import trigger_scheduler as scheduler_module

    db = FakeDB()
    trigger = Trigger(
        name="http-job",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 60},
        action_type="http_request",
        action_config={"url": "https://example.com/hook", "method": "POST", "body": {"ok": True}},
        next_fire_at=datetime.now(UTC),
    )
    db.add(trigger)

    calls: list[dict] = []

    class _Response:
        def __init__(self, status_code: int):
            self.status_code = status_code

    class _Client:
        def __init__(self, timeout: float):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def request(self, method: str, url: str, **kwargs):
            calls.append({"method": method, "url": url, "kwargs": kwargs})
            return _Response(202)

    old_client = scheduler_module.httpx.AsyncClient
    scheduler_module.httpx.AsyncClient = _Client
    try:
        scheduler = TriggerScheduler(
            agent_loop=None,
            tool_executor=None,
            db_factory=_SessionFactory(db),
            poll_interval_seconds=0.01,
        )
        _run(scheduler._fire_trigger(trigger.id))
    finally:
        scheduler_module.httpx.AsyncClient = old_client

    assert calls
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == "https://example.com/hook"
    assert trigger.fire_count == 1


def test_scheduler_auto_disables_after_five_consecutive_errors():
    db = FakeDB()
    trigger = Trigger(
        name="bad-tool",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 30},
        action_type="tool_call",
        action_config={},
        next_fire_at=datetime.now(UTC),
    )
    db.add(trigger)

    scheduler = TriggerScheduler(
        agent_loop=None,
        tool_executor=_ToolExecutorStub(),
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )
    for _ in range(5):
        _run(scheduler._fire_trigger(trigger.id))

    assert trigger.enabled is False
    assert trigger.error_count >= 5
    assert trigger.consecutive_errors >= 5
    failed_logs = [item for item in db.storage[TriggerLog] if item.status == "failed"]
    assert len(failed_logs) == 5


def test_scheduler_start_polls_due_triggers_and_stops():
    db = FakeDB()
    trigger = Trigger(
        name="poll-me",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 120},
        action_type="tool_call",
        action_config={"tool_name": "x", "payload": {}},
        next_fire_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(trigger)

    tools = _ToolExecutorStub()
    scheduler = TriggerScheduler(
        agent_loop=None,
        tool_executor=tools,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )

    async def _scenario():
        stop_event = asyncio.Event()
        task = asyncio.create_task(scheduler.start(stop_event))
        await asyncio.sleep(0.05)
        stop_event.set()
        await task

    _run(_scenario())

    assert tools.calls
    assert trigger.fire_count >= 1


def test_compute_next_fire_at_invalid_cron_raises_value_error():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    try:
        compute_next_fire_at("cron", {"expr": "not-a-cron"}, reference_time=now)
        raised = False
    except ValueError:
        raised = True
    assert raised is True


def test_compute_next_fire_at_webhook_returns_none():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert compute_next_fire_at("webhook", {"secret": "x"}, reference_time=now) is None


def test_scheduler_ignores_trigger_already_in_flight():
    db = FakeDB()
    trigger = Trigger(
        name="already-running",
        type="heartbeat",
        enabled=True,
        config={"interval_seconds": 60},
        action_type="tool_call",
        action_config={"tool_name": "x", "payload": {}},
        next_fire_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(trigger)

    tools = _ToolExecutorStub()
    scheduler = TriggerScheduler(
        agent_loop=None,
        tool_executor=tools,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )
    scheduler._in_flight.add(str(trigger.id))
    _run(scheduler._poll_once())
    assert not tools.calls


def test_scheduler_cancellation_advances_next_fire_and_marks_log_cancelled():
    db = FakeDB()
    session = Session(user_id="user-1", title="Main", status="active")
    db.add(session)
    trigger = Trigger(
        name="hourly-agent",
        user_id="user-1",
        type="cron",
        enabled=True,
        config={"expr": "0 * * * *"},
        action_type="agent_message",
        action_config={"message": "ping", "session_id": str(session.id)},
        next_fire_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(trigger)

    run_registry = AgentRunRegistry()
    scheduler = TriggerScheduler(
        agent_loop=_BlockingAgentLoopStub(),
        tool_executor=None,
        run_registry=run_registry,
        db_factory=_SessionFactory(db),
        poll_interval_seconds=0.01,
    )

    async def _scenario():
        task = asyncio.create_task(scheduler._fire_trigger(trigger.id))
        for _ in range(20):
            if await run_registry.is_running(str(session.id)):
                break
            await asyncio.sleep(0.01)
        assert await run_registry.is_running(str(session.id)) is True
        cancelled = await run_registry.cancel(str(session.id))
        assert cancelled is True
        await task

    _run(_scenario())

    assert trigger.enabled is True
    assert trigger.next_fire_at is not None
    assert trigger.next_fire_at > datetime.now(UTC)
    logs = db.storage[TriggerLog]
    assert logs
    assert logs[-1].status == "cancelled"
