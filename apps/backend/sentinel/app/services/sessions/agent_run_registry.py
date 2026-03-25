from __future__ import annotations

import asyncio


class AgentRunRegistry:
    """Tracks active agent runs keyed by parent session id."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._lock = asyncio.Lock()

    async def register(self, session_id: str, task: asyncio.Task[object]) -> bool:
        async with self._lock:
            current = self._tasks.get(session_id)
            if current is not None and not current.done():
                return False
            self._tasks[session_id] = task
            return True

    async def clear(self, session_id: str, task: asyncio.Task[object] | None = None) -> None:
        async with self._lock:
            current = self._tasks.get(session_id)
            if current is None:
                return
            if task is not None and current is not task:
                return
            self._tasks.pop(session_id, None)

    async def cancel(self, session_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(session_id)
        if task is None or task.done():
            return False
        task.cancel("cancelled by user")
        return True

    async def cancel_and_wait(self, session_id: str, *, timeout_seconds: float = 2.0) -> bool:
        async with self._lock:
            task = self._tasks.get(session_id)
        if task is None:
            return False
        if task.done():
            await self.clear(session_id, task)
            return False

        task.cancel("cancelled by user")
        try:
            await asyncio.wait_for(task, timeout=max(0.05, float(timeout_seconds)))
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        finally:
            if task.done():
                await self.clear(session_id, task)
        return True

    async def is_running(self, session_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(session_id)
            return task is not None and not task.done()
