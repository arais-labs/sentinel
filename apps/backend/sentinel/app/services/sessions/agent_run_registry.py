from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from sentral import ConversationItem


class AgentRunRegistry:
    """Tracks active agent runs keyed by parent session id."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._lock = asyncio.Lock()
        self._interjections: dict[str, list[ConversationItem]] = {}
        self._on_idle_interjections: Callable[[str], Awaitable[None]] | None = None
        self._phases: dict[str, str] = {}
        self._shutting_down = False

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

    def peek_interjections(self, session_id: str) -> list[ConversationItem]:
        return list(self._interjections.get(session_id, []))

    def discard_steering(self, session_id: str) -> None:
        self._interjections[session_id] = [
            item
            for item in self._interjections.get(session_id, [])
            if not item.metadata.get("steering")
        ]

    async def notify_idle_interjections(self, session_id: str) -> None:
        if (
            not self._shutting_down
            and self.has_interjections(session_id)
            and not await self.is_running(session_id)
        ):
            if self._on_idle_interjections is not None:
                await self._on_idle_interjections(session_id)

    def has_interjections(self, session_id: str) -> bool:
        queued = self._interjections.get(session_id)
        return bool(queued)

    @asynccontextmanager
    async def workspace_change_guard(self):
        """Hold run registration and attachment changes while inspecting bindings."""
        async with self._lock:
            yield {key for key, task in self._tasks.items() if not task.done()}

    @asynccontextmanager
    async def idle_guard(self, session_id: str):
        """Serialize workspace changes with new agent-run registration."""
        async with self.workspace_change_guard() as running:
            yield session_id not in running

    async def register(self, session_id: str, task: asyncio.Task[object]) -> bool:
        async with self._lock:
            current = self._tasks.get(session_id)
            if current is not None and not current.done():
                return False
            self._tasks[session_id] = task
            self._phases[session_id] = "thinking"
            return True

    async def start(
        self, session_id: str, run: Coroutine[Any, Any, Any], *, require_interjections: bool = False
    ) -> asyncio.Task | None:
        """Create the task only after workspace changes and other starts finish."""
        try:
            async with self._lock:
                current = self._tasks.get(session_id)
                if self._shutting_down or (
                    require_interjections and not self.has_interjections(session_id)
                ):
                    run.close()
                    return None
                if current is not None and not current.done():
                    run.close()
                    return None
                task = asyncio.create_task(run)
                self._tasks[session_id] = task
                self._phases[session_id] = "thinking"
                return task
        except BaseException:
            run.close()
            raise

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
        if notify_idle and callback is not None and not self._shutting_down:
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

    async def cancel_all(self, *, timeout_seconds: float = 3.0) -> int:
        """Cancel every active agent run and wait for them with a hard deadline.

        Used by the FastAPI lifespan `finally` so an in-flight stream can't
        wedge the whole reload. Returns the count of runs that were live at
        cancel time. Any task that ignores cancellation past the deadline is
        abandoned to process exit — acceptable because nothing on disk depends
        on a clean turn boundary, and the next process owns a fresh registry.
        """
        async with self._lock:
            self._shutting_down = True
            live = [task for task in self._tasks.values() if not task.done()]
        if not live:
            return 0
        for task in live:
            task.cancel("server shutting down")
        try:
            await asyncio.wait_for(
                asyncio.gather(*live, return_exceptions=True),
                timeout=max(0.1, float(timeout_seconds)),
            )
        except asyncio.TimeoutError:
            pass
        return len(live)

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
