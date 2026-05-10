import uuid
from datetime import UTC, datetime

import pytest

from app.services.araios.system_modules.port_forward import handlers as port_forward_handlers
from app.services.runtime.port_forwards import RuntimeForwardRecord, serialize_forward
from app.services.tools.registry import ToolRuntimeContext


@pytest.mark.asyncio
async def test_port_forward_open_uses_runtime_session_id(monkeypatch):
    parent_session_id = uuid.uuid4()
    child_session_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def _fake_ensure_runtime_forward(
        *,
        runtime_session_id,
        created_session_id,
        target_host,
        target_port,
        protocol,
        label,
    ):
        captured.update(
            runtime_session_id=runtime_session_id,
            created_session_id=created_session_id,
            target_host=target_host,
            target_port=target_port,
            protocol=protocol,
            label=label,
        )
        return RuntimeForwardRecord(
            forward_id="forward-1",
            runtime_session_id=str(runtime_session_id),
            created_session_id=str(created_session_id),
            target_host=target_host,
            target_port=target_port,
            relay_port=12000,
            protocol=protocol,
            label=label,
            status="open",
            relay_pid=999,
            created_at=datetime.now(UTC).isoformat(),
        )

    monkeypatch.setattr(port_forward_handlers, "ensure_runtime_forward", _fake_ensure_runtime_forward)
    monkeypatch.setattr(
        "app.services.runtime.port_forwards.get_runtime",
        lambda: type(
            "StubRuntimeProvider",
            (),
            {
                "get_public_host": lambda self, session_id: "localhost",
                "resolve_port": lambda self, session_id, internal_port: 15421,
            },
        )(),
    )

    result = await port_forward_handlers.handle_open(
        {"port": 3000, "label": "Vite"},
        ToolRuntimeContext(session_id=child_session_id, runtime_session_id=parent_session_id),
    )

    assert captured["runtime_session_id"] == parent_session_id
    assert captured["created_session_id"] == child_session_id
    assert result["forward_id"] == "forward-1"
    assert result["url"] == "http://localhost:15421/"
    assert result["host"] == "localhost"
    assert result["host_port"] == 15421
    assert result["port"] == 3000


@pytest.mark.asyncio
async def test_port_forward_open_supports_tcp(monkeypatch):
    session_id = uuid.uuid4()

    async def _fake_ensure_runtime_forward(
        *,
        runtime_session_id,
        created_session_id,
        target_host,
        target_port,
        protocol,
        label,
    ):
        _ = created_session_id, label
        return RuntimeForwardRecord(
            forward_id="forward-tcp",
            runtime_session_id=str(runtime_session_id),
            created_session_id=str(session_id),
            target_host=target_host,
            target_port=target_port,
            relay_port=12002,
            protocol=protocol,
            label="postgres",
            status="open",
            relay_pid=333,
            created_at=datetime.now(UTC).isoformat(),
        )

    monkeypatch.setattr(port_forward_handlers, "ensure_runtime_forward", _fake_ensure_runtime_forward)
    monkeypatch.setattr(
        "app.services.runtime.port_forwards.get_runtime",
        lambda: type(
            "StubRuntimeProvider",
            (),
            {
                "get_public_host": lambda self, session_id: "localhost",
                "resolve_port": lambda self, session_id, internal_port: 15432,
            },
        )(),
    )

    result = await port_forward_handlers.handle_open(
        {"port": 5432, "protocol": "tcp", "label": "postgres"},
        ToolRuntimeContext(session_id=session_id, runtime_session_id=session_id),
    )

    assert result["protocol"] == "tcp"
    assert result["url"] == "tcp://localhost:15432"
    assert result["host_port"] == 15432
    assert result["port"] == 5432


@pytest.mark.asyncio
async def test_port_forward_list_returns_serialized_live_forwards(monkeypatch):
    runtime_session = uuid.uuid4()
    records = [
        RuntimeForwardRecord(
            forward_id="forward-a",
            runtime_session_id=str(runtime_session),
            created_session_id=None,
            target_host="127.0.0.1",
            target_port=5173,
            relay_port=12000,
            protocol="http",
            label="Vite",
            status="open",
            relay_pid=101,
            created_at=datetime.now(UTC).isoformat(),
        ),
        RuntimeForwardRecord(
            forward_id="forward-b",
            runtime_session_id=str(runtime_session),
            created_session_id=None,
            target_host="127.0.0.1",
            target_port=5432,
            relay_port=12001,
            protocol="tcp",
            label="postgres",
            status="open",
            relay_pid=102,
            created_at=datetime.now(UTC).isoformat(),
        ),
    ]

    async def _fake_list_runtime_forwards(*, runtime_session_id):
        assert runtime_session_id == runtime_session
        return records

    monkeypatch.setattr(port_forward_handlers, "list_runtime_forwards", _fake_list_runtime_forwards)
    monkeypatch.setattr(
        "app.services.runtime.port_forwards.get_runtime",
        lambda: type(
            "StubRuntimeProvider",
            (),
            {
                "get_public_host": lambda self, session_id: "localhost",
                "resolve_port": lambda self, session_id, internal_port: 15421 if internal_port == 12000 else 15432,
            },
        )(),
    )

    result = await port_forward_handlers.handle_list(
        {},
        ToolRuntimeContext(session_id=runtime_session, runtime_session_id=runtime_session),
    )

    assert result["runtime_session_id"] == str(runtime_session)
    assert len(result["forwards"]) == 2
    assert result["forwards"][0]["url"] == "http://localhost:15421/"
    assert result["forwards"][1]["url"] == "tcp://localhost:15432"


@pytest.mark.asyncio
async def test_port_forward_close_uses_runtime_owned_state(monkeypatch):
    runtime_session = uuid.uuid4()
    closed_record = RuntimeForwardRecord(
        forward_id="forward-close",
        runtime_session_id=str(runtime_session),
        created_session_id=None,
        target_host="127.0.0.1",
        target_port=8000,
        relay_port=12003,
        protocol="http",
        label="api",
        status="open",
        relay_pid=111,
        created_at=datetime.now(UTC).isoformat(),
    )

    async def _fake_close_runtime_forward(*, runtime_session_id, forward_id):
        assert str(runtime_session_id) == str(runtime_session)
        assert forward_id == "forward-close"
        return closed_record

    monkeypatch.setattr(port_forward_handlers, "close_runtime_forward", _fake_close_runtime_forward)
    monkeypatch.setattr(
        "app.services.runtime.port_forwards.get_runtime",
        lambda: type(
            "StubRuntimeProvider",
            (),
            {
                "get_public_host": lambda self, session_id: "localhost",
                "resolve_port": lambda self, session_id, internal_port: 15423,
            },
        )(),
    )

    result = await port_forward_handlers.handle_close(
        {"forward_id": "forward-close"},
        ToolRuntimeContext(session_id=runtime_session, runtime_session_id=runtime_session),
    )

    assert result["forward_id"] == "forward-close"
    assert result["url"] == "http://localhost:15423/"
    assert result["host_port"] == 15423


def test_serialize_forward_uses_runtime_published_host_port(monkeypatch):
    runtime_session_id = uuid.uuid4()
    record = RuntimeForwardRecord(
        forward_id="forward-serialize",
        runtime_session_id=str(runtime_session_id),
        created_session_id=None,
        target_host="127.0.0.1",
        target_port=5173,
        relay_port=12000,
        protocol="http",
        label="vite",
        status="open",
        relay_pid=123,
        created_at=datetime.now(UTC).isoformat(),
    )

    monkeypatch.setattr(
        "app.services.runtime.port_forwards.get_runtime",
        lambda: type(
            "StubRuntimeProvider",
            (),
            {
                "get_public_host": lambda self, session_id: "localhost",
                "resolve_port": lambda self, session_id, internal_port: 15421,
            },
        )(),
    )

    payload = serialize_forward(record)

    assert payload.forward_id == "forward-serialize"
    assert payload.host_port == 15421
    assert payload.url == "http://localhost:15421/"


def test_serialize_forward_uses_provider_public_host(monkeypatch):
    runtime_session_id = uuid.uuid4()
    record = RuntimeForwardRecord(
        forward_id="forward-runtime",
        runtime_session_id=str(runtime_session_id),
        created_session_id=None,
        target_host="127.0.0.1",
        target_port=5173,
        relay_port=12000,
        protocol="http",
        label="vite",
        status="open",
        relay_pid=123,
        created_at=datetime.now(UTC).isoformat(),
    )

    monkeypatch.setattr(
        "app.services.runtime.port_forwards.get_runtime",
        lambda: type(
            "StubRuntimeProvider",
            (),
            {
                "get_public_host": lambda self, session_id: "192.168.64.7",
                "resolve_port": lambda self, session_id, internal_port: 12000,
            },
        )(),
    )

    payload = serialize_forward(record)

    assert payload.host == "192.168.64.7"
    assert payload.host_port == 12000
    assert payload.url == "http://192.168.64.7:12000/"
