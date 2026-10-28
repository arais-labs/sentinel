"""Remote startup readiness: sentineld answers on its socket once its state is loaded.

Only a refused connection (nothing listening yet) is retried. A sentineld that answers but
rejects the handshake is a real failure, never retried or masked by a later probe.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncssh
import pytest

from app.services.workspaces.machines.remote import worker as remote


def connection(replies, refusals=0):
    """An SSH connection whose socket refuses `refusals` times, then answers with `replies`."""
    attempts = []
    writers = []

    async def open_unix_connection(path, encoding=None):
        attempts.append(path)
        if len(attempts) <= refusals:
            raise asyncssh.ChannelOpenError(2, "Connection refused")
        lines = iter(replies)
        writer = SimpleNamespace(write=Mock(), close=Mock())
        writers.append(writer)
        return SimpleNamespace(readline=AsyncMock(side_effect=lambda: next(lines, ""))), writer

    conn = SimpleNamespace(
        open_unix_connection=open_unix_connection,
        run=AsyncMock(return_value=SimpleNamespace(stdout="", exit_status=0)),
    )
    return conn, attempts, writers


@pytest.mark.asyncio
async def test_refused_socket_is_retried_until_sentineld_answers():
    conn, attempts, writers = connection(['{"id": 1, "api": 1}\n'], refusals=3)
    await remote.wait_for_runtime_ready(conn, "/root", timeout=5, retry_interval=0)
    assert attempts == ["/root/state/sentineld.sock"] * 4
    writers[0].close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replies",
    [
        ['{"id": 1, "error": {"code": "unsupported", "message": "api version"}}\n'],
        [""],  # EOF before the handshake reply
    ],
)
async def test_answering_sentineld_failure_is_not_retried(replies):
    conn, attempts, writers = connection(replies)
    with pytest.raises(remote.RemoteMacError) as error:
        await remote.wait_for_runtime_ready(conn, "/root", timeout=5, retry_interval=0)
    assert not isinstance(error.value, remote.RuntimeUnavailable)
    assert len(attempts) == 1
    writers[0].close.assert_called_once()


@pytest.mark.asyncio
async def test_repeated_refusal_has_a_deadline():
    conn, attempts, _ = connection([], refusals=10**9)
    with pytest.raises(remote.RemoteMacError, match="Timed out waiting for the remote runtime"):
        await remote.wait_for_runtime_ready(conn, "/root", timeout=0.2, retry_interval=0.01)
    assert len(attempts) > 1


@pytest.mark.asyncio
async def test_cancellation_closes_the_connection():
    started = asyncio.Event()
    writer = SimpleNamespace(write=Mock(), close=Mock())

    async def never():
        started.set()
        await asyncio.Event().wait()

    async def open_unix_connection(path, encoding=None):
        return SimpleNamespace(readline=never), writer

    conn = SimpleNamespace(open_unix_connection=open_unix_connection, run=AsyncMock())
    task = asyncio.create_task(remote.wait_for_runtime_ready(conn, "/root", timeout=5))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    writer.close.assert_called_once()
