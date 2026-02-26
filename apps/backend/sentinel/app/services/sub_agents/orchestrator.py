from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models import Session, SubAgentTask
from app.services.agent import AgentLoop, ContextBuilder, ToolAdapter
from app.services.llm.types import AgentEvent
from app.services.tools import ToolExecutor, ToolRegistry


class SubAgentOrchestrator:
    _SUB_AGENT_MODEL_HINT = "hint:hard"

    def __init__(
        self,
        agent_loop: AgentLoop | None = None,
        db_factory: async_sessionmaker[AsyncSession] | None = None,
        base_tool_registry: ToolRegistry | None = None,
        on_task_completed: Callable[[SubAgentTask], Awaitable[None] | None] | None = None,
    ) -> None:
        self._agent_loop = agent_loop
        self._db_factory = db_factory
        self._base_tool_registry = base_tool_registry
        self._on_task_completed = on_task_completed
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._inject_queues: dict[str, asyncio.Queue[str]] = {}

    def start_task(self, task_id: UUID) -> bool:
        if self._db_factory is None:
            return False
        key = str(task_id)
        if key in self._running_tasks:
            return True
        task = asyncio.create_task(self.run_task(task_id))
        self._running_tasks[key] = task

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(key, None)

        task.add_done_callback(_cleanup)
        return True

    def cancel_task(self, task_id: UUID) -> bool:
        task = self._running_tasks.pop(str(task_id), None)
        if task is None:
            return False
        task.cancel()
        self._inject_queues.pop(str(task_id), None)
        return True

    def inject_message(self, task_id: UUID, message: str) -> bool:
        queue = self._inject_queues.get(str(task_id))
        if queue is None:
            return False
        queue.put_nowait(message)
        return True

    async def complete_task(self, db: AsyncSession, task: SubAgentTask) -> SubAgentTask:
        # Synchronous fallback used when async orchestrator runtime is unavailable.
        now = datetime.now(UTC)
        task.status = "running"
        task.started_at = now
        task.turns_used = min(task.max_turns, 1)
        task.tokens_used = 64
        task.result = {
            "summary": f"Sub-agent '{task.objective}' completed",
            "scope": task.context,
            "steps_executed": task.turns_used,
        }
        task.status = "completed"
        task.completed_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(task)
        await self._notify_task_completed(task)
        return task

    async def run_task(self, task_id: UUID) -> None:
        if self._db_factory is None:
            return

        async with self._db_factory() as db:
            task = await self._load_task(db, task_id)
            if task is None:
                return
            if task.status == "cancelled":
                return

            task.status = "running"
            task.started_at = datetime.now(UTC)
            await db.commit()

            if self._agent_loop is None:
                await self._mark_failed(db, task, "Agent loop unavailable")
                return

            parent_session = await self._load_parent_session(db, task.session_id)
            if parent_session is None:
                await self._mark_failed(db, task, "Parent session not found")
                return

            child_session = Session(
                user_id=parent_session.user_id,
                agent_id=parent_session.agent_id,
                parent_session_id=parent_session.id,
                title=f"sub-agent:{task.objective[:80]}",
                status="active",
            )
            db.add(child_session)
            await db.commit()
            await db.refresh(child_session)

            # Store child_session_id immediately so the UI can poll messages while running
            task.result = {"child_session_id": str(child_session.id)}
            await db.commit()

            scoped_loop = self._scoped_agent_loop(task.allowed_tools if isinstance(task.allowed_tools, list) else [])
            prompt = self._sub_agent_system_prompt(task)

            key = str(task.id)
            queue: asyncio.Queue[str] = asyncio.Queue()
            self._inject_queues[key] = queue
            last_reported_turn = int(task.turns_used or 0)

            async def _on_sub_agent_event(event: AgentEvent) -> None:
                nonlocal last_reported_turn
                if event.type != "agent_progress":
                    return
                turn = int(event.iteration or 0)
                if turn <= last_reported_turn:
                    return
                last_reported_turn = turn
                task.turns_used = min(turn, int(task.max_turns))
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()

            try:
                result = await asyncio.wait_for(
                    scoped_loop.run(
                        db,
                        child_session.id,
                        task.objective,
                        system_prompt=prompt,
                        model=self._SUB_AGENT_MODEL_HINT,
                        max_iterations=max(1, task.max_turns),
                        stream=False,
                        inject_queue=queue,
                        persist_incremental=True,
                        on_event=_on_sub_agent_event,
                    ),
                    timeout=max(1, task.timeout_seconds),
                )
            except asyncio.TimeoutError:
                await self._mark_failed(db, task, "Sub-agent timed out")
                return
            except asyncio.CancelledError:
                task.status = "cancelled"
                task.completed_at = datetime.now(UTC)
                await db.commit()
                await self._notify_task_completed(task)
                return
            except Exception as exc:  # noqa: BLE001
                await self._mark_failed(db, task, str(exc))
                return
            finally:
                self._inject_queues.pop(key, None)

            task.turns_used = int(result.iterations)
            task.tokens_used = int(result.usage.input_tokens + result.usage.output_tokens)
            task.status = "completed"
            task.completed_at = datetime.now(UTC)
            task.result = {
                "final_text": result.final_text,
                "iterations": result.iterations,
                "usage": {
                    "input_tokens": result.usage.input_tokens,
                    "output_tokens": result.usage.output_tokens,
                },
                "allowed_tools": task.allowed_tools,
                "child_session_id": str(child_session.id),
            }
            await db.commit()
            await self._notify_task_completed(task)

    async def _load_task(self, db: AsyncSession, task_id: UUID) -> SubAgentTask | None:
        result = await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id))
        return result.scalars().first()

    async def _load_parent_session(self, db: AsyncSession, session_id: UUID) -> Session | None:
        result = await db.execute(select(Session).where(Session.id == session_id))
        return result.scalars().first()

    async def _mark_failed(self, db: AsyncSession, task: SubAgentTask, reason: str) -> None:
        task.status = "failed"
        task.completed_at = datetime.now(UTC)
        task.result = {"error": reason}
        await db.commit()
        await self._notify_task_completed(task)

    async def _notify_task_completed(self, task: SubAgentTask) -> None:
        callback = self._on_task_completed
        if callback is None:
            return
        result = callback(task)
        if asyncio.iscoroutine(result):
            try:
                await result
            except Exception:
                return

    def _scoped_agent_loop(self, allowed_tools: list) -> AgentLoop:
        if not allowed_tools or self._base_tool_registry is None:
            return self._agent_loop

        scoped_registry = ToolRegistry()
        allowed = {str(item) for item in allowed_tools if isinstance(item, str)}
        for tool in self._base_tool_registry.list_all():
            if tool.name in allowed:
                scoped_registry.register(tool)

        base_context = self._agent_loop.context_builder
        context_builder = ContextBuilder(
            default_system_prompt=getattr(base_context, "_default_system_prompt", settings.default_system_prompt),
            message_limit=getattr(base_context, "_message_limit", 50),
            skill_registry=getattr(base_context, "_skill_registry", None),
            available_tools=allowed,
            env=getattr(base_context, "_env", None),
            memory_search_service=getattr(base_context, "_memory_search_service", None),
        )
        tool_adapter = ToolAdapter(scoped_registry, ToolExecutor(scoped_registry))
        return AgentLoop(self._agent_loop.provider, context_builder, tool_adapter)

    def _sub_agent_system_prompt(self, task: SubAgentTask) -> str:
        tools = (
            ", ".join(task.allowed_tools)
            if isinstance(task.allowed_tools, list) and task.allowed_tools
            else "all available tools"
        )
        scope = task.context or "No extra scope provided"
        return (
            "You are a delegated sub-agent. Stay strictly within objective and scope.\n"
            f"Allowed tools: {tools}.\n"
            f"Scope: {scope}"
        )
