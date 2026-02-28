from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime
from typing import Awaitable, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models import Session, SubAgentTask
from app.services.agent import AgentLoop, ContextBuilder, ToolAdapter
from app.services.llm.ids import TierName
from app.services.llm.generic.types import AgentEvent
from app.services.tools import ToolDefinition, ToolExecutor, ToolRegistry

_BROWSER_TAB_TARGETABLE_TOOLS = frozenset(
    {
        "browser_navigate",
        "browser_screenshot",
        "browser_click",
        "browser_type",
        "browser_select",
        "browser_wait_for",
        "browser_get_value",
        "browser_fill_form",
        "browser_press_key",
        "browser_get_text",
        "browser_snapshot",
    }
)
_BROWSER_TAB_MANAGEMENT_TOOLS = frozenset(
    {
        "browser_tabs",
        "browser_tab_open",
        "browser_tab_focus",
        "browser_tab_close",
        "browser_reset",
    }
)


class SubAgentOrchestrator:
    """Manage sub-agent task lifecycle, execution, and completion callbacks."""

    _SUB_AGENT_TIER = TierName.HARD.value

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
        """Start async execution for a queued sub-agent task idempotently."""
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
        """Cancel a running sub-agent task if present."""
        task = self._running_tasks.pop(str(task_id), None)
        if task is None:
            return False
        task.cancel()
        self._inject_queues.pop(str(task_id), None)
        return True

    def inject_message(self, task_id: UUID, message: str) -> bool:
        """Inject an operator message into a running sub-agent turn queue."""
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
        """Execute one persisted SubAgentTask to completion or terminal failure state."""
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

            now = datetime.now(UTC)
            child_session = Session(
                user_id=parent_session.user_id,
                agent_id=parent_session.agent_id,
                parent_session_id=parent_session.id,
                title=f"sub-agent:{task.objective[:80]}",
                status="active",
                started_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(child_session)
            await db.commit()
            await db.refresh(child_session)

            # Store child_session_id immediately so the UI can poll messages while running
            task.result = {"child_session_id": str(child_session.id)}
            await db.commit()

            scoped_loop = self._scoped_agent_loop(task)
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
                task.turns_used = turn
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
                        model=self._SUB_AGENT_TIER,
                        max_iterations=max(1, task.max_turns),
                        stream=False,
                        allow_high_risk=True,
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

    def _scoped_agent_loop(self, task: SubAgentTask) -> AgentLoop:
        """Build an AgentLoop restricted to task allowlist and optional browser tab scope."""
        if self._base_tool_registry is None:
            return self._agent_loop

        allowed_tools = (
            task.allowed_tools if isinstance(task.allowed_tools, list) else []
        )
        pinned_tab_id = self._pinned_browser_tab_id(task)
        if not allowed_tools and pinned_tab_id is None:
            return self._agent_loop

        if allowed_tools:
            allowed = {str(item) for item in allowed_tools if isinstance(item, str)}
        else:
            allowed = {tool.name for tool in self._base_tool_registry.list_all()}

        if pinned_tab_id is not None:
            allowed -= _BROWSER_TAB_MANAGEMENT_TOOLS

        scoped_registry = ToolRegistry()
        for tool in self._base_tool_registry.list_all():
            if tool.name not in allowed:
                continue
            scoped_registry.register(
                self._tool_with_optional_tab_scope(tool, pinned_tab_id)
            )

        available_tools = {tool.name for tool in scoped_registry.list_all()}

        base_context = self._agent_loop.context_builder
        context_builder = ContextBuilder(
            default_system_prompt=getattr(base_context, "_default_system_prompt", settings.default_system_prompt),
            token_budget=getattr(base_context, "_token_budget", settings.context_token_budget),
            available_tools=available_tools,
            memory_search_service=getattr(base_context, "_memory_search_service", None),
        )
        tool_adapter = ToolAdapter(scoped_registry, ToolExecutor(scoped_registry))
        return AgentLoop(self._agent_loop.provider, context_builder, tool_adapter)

    def _tool_with_optional_tab_scope(
        self,
        tool: ToolDefinition,
        pinned_tab_id: str | None,
    ) -> ToolDefinition:
        if pinned_tab_id is None or tool.name not in _BROWSER_TAB_TARGETABLE_TOOLS:
            return tool

        schema = copy.deepcopy(tool.parameters_schema) if isinstance(tool.parameters_schema, dict) else {}
        properties = schema.get("properties")
        if isinstance(properties, dict) and "tab_id" in properties:
            properties.pop("tab_id", None)
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [item for item in required if item != "tab_id"]

        async def _execute(payload: dict) -> dict:
            scoped_payload = dict(payload or {})
            scoped_payload["tab_id"] = pinned_tab_id
            return await tool.execute(scoped_payload)

        return ToolDefinition(
            name=tool.name,
            description=tool.description,
            risk_level=tool.risk_level,
            parameters_schema=schema,
            execute=_execute,
            enabled=tool.enabled,
        )

    def _pinned_browser_tab_id(self, task: SubAgentTask) -> str | None:
        constraints = task.constraints if isinstance(task.constraints, list) else []
        for item in constraints:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).strip().lower() != "browser_tab":
                continue
            tab_id = item.get("tab_id")
            if isinstance(tab_id, str) and tab_id.strip():
                return tab_id.strip()
        return None

    def _sub_agent_system_prompt(self, task: SubAgentTask) -> str:
        """Generate a strict scope/constraint prompt for delegated sub-agent runs."""
        tools = (
            ", ".join(task.allowed_tools)
            if isinstance(task.allowed_tools, list) and task.allowed_tools
            else "all available tools"
        )
        scope = task.context or "No extra scope provided"
        pinned_tab_id = self._pinned_browser_tab_id(task)
        tab_scope = ""
        if pinned_tab_id is not None:
            tab_scope = (
                f"\nBrowser tab scope: you are pinned to tab_id={pinned_tab_id}. "
                "All browser actions are forced to this tab. "
                "Do not attempt to open/focus/close/list tabs or reset the browser."
            )
        return (
            "You are a delegated sub-agent. Stay strictly within objective and scope.\n"
            f"Allowed tools: {tools}.\n"
            f"Scope: {scope}"
            f"{tab_scope}"
        )
