"""Foreground handoff keeps the original command, watcher and pane lock."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.runtime import RuntimeExecResult
from app.services.runtime import panes
from app.services.runtime.terminal_manager import RuntimeTerminalManager
from app.services.runtime.workspace import WorkspaceLocation


@pytest.fixture
def execution(monkeypatch):
    transport = SimpleNamespace(
        run=AsyncMock(return_value=RuntimeExecResult(exit_status=0, stdout="0", stderr="")),
        close=AsyncMock(),
    )
    manager = RuntimeTerminalManager(
        transport, workspace_location=WorkspaceLocation("/project", "/internal/workspace")
    )
    bridge = panes.TmuxPanes(manager)
    monkeypatch.setattr(
        bridge, "require_pane", AsyncMock(return_value={"dead": False, "window_id": "@0"})
    )
    monkeypatch.setattr(bridge, "command", AsyncMock(return_value="bash"))
    monkeypatch.setattr(bridge, "input", AsyncMock())
    started, release = asyncio.Event(), asyncio.Event()

    async def watch(*args, **kwargs):
        started.set()
        await release.wait()
        return RuntimeExecResult(exit_status=0, stdout="complete", stderr="")

    monitor = AsyncMock(side_effect=watch)
    monkeypatch.setattr(manager, "_await_command_complete", monitor)
    return SimpleNamespace(
        manager=manager,
        bridge=bridge,
        monitor=monitor,
        started=started,
        release=release,
        callback=AsyncMock(),
        lock=manager._lock_for("chat", "%0"),
    )


async def drain(execution):
    await asyncio.gather(*list(execution.manager._background_tasks))
    await asyncio.sleep(0)  # Allow task registry cleanup callbacks to run.


@pytest.mark.asyncio
async def test_short_command_returns_inline_without_completion_notice(execution):
    execution.release.set()
    result = await execution.bridge.execute(
        "chat", "echo complete", pane_id="%0", on_complete=execution.callback
    )
    assert result["exit_status"] == 0 and result["stdout"] == "complete"
    assert "job_id" not in result
    assert not execution.lock.locked()
    execution.callback.assert_not_awaited()
    execution.monitor.assert_awaited_once()
    await drain(execution)
    assert not execution.manager._background_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("requested, expected", [(1900, 20), (3, 3)])
async def test_wait_expiry_hands_off_without_restarting_or_unlocking(
    execution, monkeypatch, requested, expected
):
    original_wait = asyncio.wait
    waits = []

    async def expire(tasks, *, timeout):
        waits.append(timeout)
        await execution.started.wait()
        return await original_wait(tasks, timeout=0)

    monkeypatch.setattr(panes.asyncio, "wait", expire)
    result = await execution.bridge.execute(
        "chat", "long command", pane_id="%0", timeout=requested, on_complete=execution.callback
    )
    assert waits == [expected]
    assert result["status"] == "running" and result["job_id"]
    assert execution.lock.locked()
    execution.callback.assert_not_awaited()
    with pytest.raises(ValueError, match="already running"):
        await execution.bridge.execute("chat", "do not start", pane_id="%0")
    execution.release.set()
    await drain(execution)
    execution.monitor.assert_awaited_once()
    execution.bridge.input.assert_awaited_once()
    assert "timeout" not in execution.monitor.call_args.kwargs
    execution.callback.assert_awaited_once()
    job, stdout, stderr = execution.callback.call_args.args
    assert job["id"] == result["job_id"] and job["status"] == "completed"
    assert (stdout, stderr) == ("complete", "")
    assert not execution.lock.locked()
    assert not execution.manager._background_tasks


@pytest.mark.asyncio
async def test_explicit_background_does_not_wait(execution, monkeypatch):
    wait = AsyncMock(side_effect=AssertionError("Background must not wait"))
    monkeypatch.setattr(panes.asyncio, "wait", wait)
    result = await execution.bridge.execute(
        "chat", "long command", pane_id="%0", background=True, on_complete=execution.callback
    )
    assert result["status"] == "running" and execution.lock.locked()
    wait.assert_not_awaited()
    execution.release.set()
    await drain(execution)
    execution.callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_interrupted_foreground_wait_keeps_monitoring(execution):
    waiting = asyncio.create_task(
        execution.bridge.execute(
            "chat", "long command", pane_id="%0", on_complete=execution.callback
        )
    )
    await execution.started.wait()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert execution.lock.locked()
    execution.release.set()
    await drain(execution)
    execution.callback.assert_awaited_once()
    assert not execution.lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_manager_shutdown_cleans_up_without_false_completion(execution, started):
    await execution.bridge.execute(
        "chat", "long command", pane_id="%0", background=True, on_complete=execution.callback
    )
    if started:
        await execution.started.wait()
    await execution.manager.close()
    await asyncio.sleep(0)
    assert not execution.lock.locked()
    assert not execution.manager._background_tasks
    execution.callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_is_reported_once(execution):
    execution.monitor.side_effect = RuntimeError("transport unavailable")
    result = await execution.bridge.execute(
        "chat", "long command", pane_id="%0", background=True, on_complete=execution.callback
    )
    await drain(execution)
    execution.callback.assert_awaited_once()
    job, _, stderr = execution.callback.call_args.args
    assert job["id"] == result["job_id"]
    assert job["status"] == "failed" and job["returncode"] == -1
    assert stderr == "transport unavailable"
    assert not execution.lock.locked()


@pytest.mark.asyncio
async def test_completion_at_wait_boundary_is_inline_not_reported_twice(execution, monkeypatch):
    async def complete(tasks, *, timeout):
        execution.release.set()
        await asyncio.gather(*tasks)
        return set(tasks), set()

    monkeypatch.setattr(panes.asyncio, "wait", complete)
    result = await execution.bridge.execute(
        "chat", "quick command", pane_id="%0", on_complete=execution.callback
    )
    assert result["exit_status"] == 0
    execution.callback.assert_not_awaited()
    await drain(execution)
