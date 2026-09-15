import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.runtime.remote_mac import RemoteMacError, wait_for_runtime_ready


def probe(*events, eof=True, stderr=""):
    stdout = asyncio.StreamReader()
    for event in events:
        stdout.feed_data((json.dumps(event) + "\n").encode())
    if eof:
        stdout.feed_eof()

    async def readline():
        return (await stdout.readline()).decode()

    return SimpleNamespace(
        stdout=SimpleNamespace(readline=readline),
        stderr=SimpleNamespace(read=AsyncMock(return_value=stderr)),
        close=Mock(),
        stream=stdout,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unavailable",
    [
        {"event": "unavailable", "error": "Remote runtime socket is not available yet"},
        {"event": "fatal", "error": "Remote runtime is not running"},
    ],
)
async def test_delayed_socket_then_initialization_then_ready(unavailable):
    attempts = [
        probe(unavailable),
        probe(unavailable),
        probe(
            {"event": "connected"},
            {"event": "preparing"},
            {"event": "ready"},
        ),
    ]
    conn = SimpleNamespace(create_process=AsyncMock(side_effect=attempts))
    await wait_for_runtime_ready(conn, "helper --relay socket", retry_interval=0)
    assert conn.create_process.await_count == 3
    for attempt in attempts:
        attempt.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [{"event": "fatal", "error": "Permission denied"}],
        [
            {"event": "connected"},
            {"event": "preparing"},
            {"event": "fatal", "error": "Image checksum mismatch"},
        ],
        [
            {"event": "connected"},
            {"event": "fatal", "error": "Remote runtime is not running"},
        ],
    ],
)
async def test_genuine_failure_is_not_retried(events):
    attempt = probe(*events)
    conn = SimpleNamespace(create_process=AsyncMock(return_value=attempt))
    with pytest.raises(RemoteMacError, match=events[-1]["error"]):
        await wait_for_runtime_ready(conn, "relay")
    conn.create_process.assert_awaited_once()
    attempt.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("connected", [False, True])
async def test_eof_before_ready_is_not_retried(connected):
    attempt = probe(*([{"event": "connected"}] if connected else []), stderr="helper exited")
    conn = SimpleNamespace(create_process=AsyncMock(return_value=attempt))
    with pytest.raises(RemoteMacError, match="closed before readiness: helper exited"):
        await wait_for_runtime_ready(conn, "relay")
    conn.create_process.assert_awaited_once()
    attempt.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("connected,phase", [(False, "control socket"), (True, "initialization")])
async def test_deadlines_close_probe_and_identify_phase(connected, phase):
    attempt = probe(*([{"event": "connected"}] if connected else []), eof=False)
    conn = SimpleNamespace(create_process=AsyncMock(return_value=attempt))
    with pytest.raises(RemoteMacError, match=phase):
        await wait_for_runtime_ready(conn, "relay", socket_timeout=0.02, startup_timeout=0.02)
    attempt.close.assert_called_once()


@pytest.mark.asyncio
async def test_repeated_unavailability_has_a_deadline():
    attempts = []

    async def create_process(*args, **kwargs):
        attempt = probe({"event": "unavailable"})
        attempts.append(attempt)
        return attempt

    with pytest.raises(RemoteMacError, match="control socket"):
        await wait_for_runtime_ready(
            SimpleNamespace(create_process=create_process),
            "relay",
            socket_timeout=0.02,
            retry_interval=0,
        )
    assert len(attempts) > 1
    for attempt in attempts:
        attempt.close.assert_called_once()


@pytest.mark.asyncio
async def test_connected_helper_gets_initialization_deadline():
    attempt = probe({"event": "connected"}, eof=False)
    conn = SimpleNamespace(create_process=AsyncMock(return_value=attempt))
    task = asyncio.create_task(
        wait_for_runtime_ready(
            conn,
            "relay",
            socket_timeout=0.02,
            startup_timeout=1,
        )
    )
    try:
        await asyncio.sleep(0.05)
        assert not task.done()
        attempt.stream.feed_data(b'{"event":"ready"}\n')
        await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    attempt.close.assert_called_once()


@pytest.mark.asyncio
async def test_cancellation_closes_probe():
    attempt = probe({"event": "connected"}, eof=False)
    conn = SimpleNamespace(create_process=AsyncMock(return_value=attempt))
    task = asyncio.create_task(wait_for_runtime_ready(conn, "relay"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    attempt.close.assert_called_once()
