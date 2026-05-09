from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.sentral import ConversationItem


class AgentRunRegistry:
    """Tracks active agent runs keyed by parent session id."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._lock = asyncio.Lock()
        self._interjections: dict[str, list[ConversationItem]] = {}
        self._on_idle_interjections: Callable[[str], Awaitable[None]] | None = None
        self._phases: dict[str, str] = {}

    def configure_idle_interjections_callback(
        self,
        callback: Callable[[str], Awaitable[None]] | None,
    ) -> None:
        self._on_idle_interjections = callback

    def enqueue_interjection(self, session_id: str, item: ConversationItem) -> None:
        queue = self._interjections.setdefault(session_id, [])
        queue.append(item)

    def drain_interjections(self, session_id: str) -> list[ConversationItem]:
        queued = self._interjections.get(session_id)
        if not queued:
            return []
        self._interjections.pop(session_id, None)
        return list(queued)

    def has_interjections(self, session_id: str) -> bool:
        queued = self._interjections.get(session_id)
        return bool(queued)

    async def register(self, session_id: str, task: asyncio.Task[object]) -> bool:
        async with self._lock:
            current = self._tasks.get(session_id)
            if current is not None and not current.done():
                return False
            self._tasks[session_id] = task
            self._phases[session_id] = "thinking"
            return True

    async def clear(self, session_id: str, task: asyncio.Task[object] | None = None) -> None:
        notify_idle = False
        callback = self._on_idle_interjections
        async with self._lock:
            current = self._tasks.get(session_id)
            if current is None:
                return
            if task is not None and current is not task:
                return
            self._tasks.pop(session_id, None)
            self._phases.pop(session_id, None)
            notify_idle = bool(self._interjections.get(session_id))
        if notify_idle and callback is not None:
            await callback(session_id)

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

    async def set_phase(self, session_id: str, phase: str | None) -> None:
        async with self._lock:
            task = self._tasks.get(session_id)
            if task is None or task.done():
                self._phases.pop(session_id, None)
                return
            if phase is None:
                self._phases.pop(session_id, None)
                return
            self._phases[session_id] = str(phase)

    async def get_phase(self, session_id: str) -> str | None:
        async with self._lock:
            task = self._tasks.get(session_id)
            if task is None or task.done():
                return None
            return self._phases.get(session_id)
