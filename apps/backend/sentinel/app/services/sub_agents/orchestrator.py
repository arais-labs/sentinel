from __future__ import annotations

from app.services.sessions.compaction import CompactionService

import asyncio
from datetime import UTC, datetime
from typing import Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models import Message, Session, SubAgentTask
from sentral import (
    AgentEvent,
    ConversationItem,
    GenerationConfig,
    RunTurnRequest,
    TextBlock,
)
import app.services.agent.context_builder as context_builder_module
import app.services.agent.runtime_support as runtime_support_module
import app.services.agent_runtime_adapters.runtime as runtime_adapters
from app.services.sub_agents.accounting import inherited_model, task_usage
from app.services.sub_agents.messaging import send_message
from app.services.tools import ToolExecutor, ToolRegistry
from app.services.tools.approval.approval_waiters import (
    build_tool_db_approval_result_recorder,
    build_tool_db_approval_waiter,
)
from sentral.errors import ToolValidationError

_SUB_AGENT_EXCLUDED_TOOLS = frozenset({"delegate"})


class SubAgentOrchestrator:
    """Durable child conversations using the main agent's runtime and accounting."""

    def __init__(
        self,
        agent_runtime_support: runtime_support_module.SentinelRuntimeSupport | None = None,
        db_factory: async_sessionmaker[AsyncSession] | None = None,
        base_tool_registry: ToolRegistry | None = None,
        on_task_completed: Callable[[SubAgentTask], Awaitable[None] | None] | None = None,
    ) -> None:
        self._agent_runtime_support = agent_runtime_support
        self._db_factory = db_factory
        self._base_tool_registry = base_tool_registry
        self._on_task_completed = on_task_completed
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._inject_queues: dict[str, list[ConversationItem]] = {}
        self._closing = False
        self._resume_lock = asyncio.Lock()

    def start_task(self, task_id: UUID) -> bool:
        if self._closing or self._db_factory is None or self._agent_runtime_support is None:
            return False
        key = str(task_id)
        if key in self._running_tasks and not self._running_tasks[key].done():
            return True
        self._inject_queues.setdefault(key, [])
        task = asyncio.create_task(self._drive(task_id))
        self._running_tasks[key] = task
        task.add_done_callback(
            lambda completed: (
                self._running_tasks.pop(key, None)
                if self._running_tasks.get(key) is completed
                else None
            )
        )
        return True

    async def _drive(self, task_id: UUID) -> None:
        try:
            while True:
                await self.run_task(task_id)
                if self._closing or not self._inject_queues.get(str(task_id)):
                    break
        finally:
            self._inject_queues.pop(str(task_id), None)

    def cancel_task(self, task_id: UUID) -> bool:
        task = self._running_tasks.get(str(task_id))
        if task is None or task.done():
            return False
        self._inject_queues.pop(str(task_id), None)
        task.cancel()
        return True

    async def stop_task(self, task_id: UUID) -> bool:
        async with self._resume_lock:
            task = self._running_tasks.get(str(task_id))
            sent = self.cancel_task(task_id)
            if task:
                await asyncio.gather(task, return_exceptions=True)
            return sent

    async def close(self) -> None:
        self._closing = True
        tasks = list(self._running_tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def inject_item(self, task_id: UUID, item: ConversationItem) -> bool:
        if self._closing:
            return False
        self._inject_queues.setdefault(str(task_id), []).append(item)
        return self.start_task(task_id)

    async def resume_task(self, task_id: UUID, *, parent_id: UUID, message: str) -> dict:
        """Explicit follow-up: reuse the durable child, never replay its old tool calls."""

        if self._closing or self._db_factory is None or self._agent_runtime_support is None:
            raise ToolValidationError("Sub-agent runtime is unavailable")
        async with self._resume_lock:
            previous = self._running_tasks.get(str(task_id))
            if previous and not previous.done():
                if not previous.cancelling():
                    raise ToolValidationError(
                        "Sub-agent is already active; use agent_messages to steer it"
                    )
                # Let cancellation finish persisting the old turn before adding
                # new steering, which otherwise could be discarded by cleanup.
                await asyncio.gather(asyncio.shield(previous), return_exceptions=True)
            if self._closing:
                raise ToolValidationError("Sub-agent runtime is stopping")
            async with self._db_factory() as db:
                task = await self._load_task(db, task_id)
                if task is None or task.session_id != parent_id:
                    raise ToolValidationError("Sub-agent task not found for this session")
                if task.status not in {"cancelled", "failed", "completed"}:
                    raise ToolValidationError(
                        "Sub-agent is already active; use agent_messages to steer it"
                    )
                child_id = (task.result or {}).get("child_session_id")
                if not child_id or await db.get(Session, UUID(child_id)) is None:
                    raise ToolValidationError(
                        "This task has no saved child conversation; spawn a new agent"
                    )
                tasks = (
                    (
                        await db.execute(
                            select(SubAgentTask).where(SubAgentTask.session_id == parent_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                active = [
                    t
                    for t in tasks
                    if t.status in {"pending", "running"}
                    or (
                        str(t.id) in self._running_tasks
                        and not self._running_tasks[str(t.id)].done()
                    )
                ]
                if len(active) >= 3:
                    raise ToolValidationError("Max 3 concurrent sub-agent tasks per session")
                previous_status, completed_at = task.status, task.completed_at
                task.status = "pending"
                task.completed_at = None
                # Persist admission and the follow-up in the same transaction.
                delivered = await send_message(
                    db,
                    sender_id=parent_id,
                    target=str(task_id),
                    content=message,
                    orchestrator=self,
                )
                if delivered["delivery"] != "queued":
                    task.status, task.completed_at = previous_status, completed_at
                    item = await db.get(Message, UUID(delivered["message_id"]))
                    item.metadata_json = {**item.metadata_json, "steering": "cancelled"}
                    await db.commit()
                    raise ToolValidationError(
                        "Sub-agent runtime stopped before resuming; retry when it is ready"
                    )
                return {"task_id": str(task_id), "status": "pending", **delivered}

    async def complete_task(self, db: AsyncSession, task: SubAgentTask) -> SubAgentTask:
        # Never fabricate success or usage when execution is unavailable.
        await self._mark_failed(db, task, "Agent runtime support unavailable")
        return task

    async def run_task(self, task_id: UUID) -> None:
        if self._db_factory is None:
            return
        async with self._db_factory() as db:
            task = await self._load_task(db, task_id)
            if task is None:
                return
            parent = await self._load_parent_session(db, task.session_id)
            if parent is None or self._agent_runtime_support is None:
                await self._mark_failed(db, task, "Parent session or agent runtime unavailable")
                return
            child_id = (task.result or {}).get("child_session_id")
            child = await db.get(Session, UUID(child_id)) if child_id else None
            is_new = child is None
            if is_new:
                child = Session(
                    user_id=parent.user_id,
                    agent_id=parent.agent_id,
                    parent_session_id=parent.id,
                    title=f"sub-agent:{task.objective[:80]}",
                    status="active",
                )
                db.add(child)
                await db.flush()
            child_session_id = child.id
            task.result = {
                **(task.result or {}),
                "child_session_id": str(child_session_id),
            }
            task.result = {
                **task.result,
                "turn_id": str(uuid4()),
                "final_text": "",
                "error": None,
                "stop_reason": None,
                "cancel_reason": None,
            }
            task.model = task.model or await inherited_model(db, parent.id)
            task.status = "running"
            task.started_at = task.started_at or datetime.now(UTC)
            task.completed_at = None
            await db.commit()
            key = str(task.id)
            self._inject_queues.setdefault(key, [])
            initial_turns = task.turns_used or 0

            def drain():
                items = self._inject_queues.get(key, [])
                self._inject_queues[key] = []
                return items

            async def account():
                usage = await task_usage(db, task)
                task.tokens_used = usage["input_tokens"] + usage["output_tokens"]
                task.result = {**(task.result or {}), "usage": usage}
                await db.commit()

            async def on_event(event: AgentEvent):
                if event.type == "agent_progress":
                    task.turns_used = initial_turns + int(event.iteration or 0)
                    await db.commit()

            try:
                runtime = runtime_adapters.SentinelLoopRuntimeAdapter(
                    loop=self._scoped_runtime_support(task),
                    db=db,
                    session_id=child_session_id,
                    runtime_session_id=parent.id,
                    persist_incremental=True,
                    on_checkpoint=account,
                )
                result = await runtime.run_turn(
                    RunTurnRequest(
                        conversation_id=str(child_session_id),
                        new_items=(
                            [
                                ConversationItem(
                                    id=f"objective-{task.id}",
                                    role="user",
                                    content=[TextBlock(text=task.objective)],
                                    metadata={"notice": {"title": "Delegated task"}},
                                )
                            ]
                            if is_new
                            else []
                        ),
                        config=GenerationConfig(
                            model=task.model,
                            max_iterations=0,
                            stream=True,
                            system_prompt=self._sub_agent_system_prompt(task),
                        ),
                        timeout_seconds=0,
                        interjection_source=drain,
                    ),
                    sink=on_event,
                )
                task.turns_used = initial_turns + int(result.iterations)
                task.status = (
                    "completed"
                    if result.status == "completed"
                    else "cancelled" if result.status == "aborted" else "failed"
                )
                task.result = {
                    **(task.result or {}),
                    "final_text": (
                        str(result.metadata.get("final_text") or "")
                        if task.status == "completed"
                        else ""
                    ),
                    "stop_reason": result.stop_reason,
                    "error": result.error,
                }
            except asyncio.CancelledError:
                await db.rollback()
                task = await self._load_task(db, task_id)
                if task is None:
                    return
                task.status = "cancelled"
                # Explicit stop discards undelivered steering; a future follow-up is a new turn.
                rows = (
                    (
                        await db.execute(
                            select(Message).where(Message.session_id == child_session_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                for row in rows:
                    if (row.metadata_json or {}).get("steering") == "pending":
                        row.metadata_json = {
                            **row.metadata_json,
                            "steering": "cancelled",
                        }
            except Exception as exc:
                await db.rollback()
                task = await self._load_task(db, task_id)
                if task is None:
                    return
                task.status = "failed"
                task.result = {**(task.result or {}), "error": str(exc)}
            finally:
                if task is None:
                    return
                task.completed_at = datetime.now(UTC)
                await account()
            # Same post-turn compaction service as the main conversation, including its usage.
            if task.status == "completed":
                try:

                    await CompactionService(
                        provider=self._agent_runtime_support.provider
                    ).auto_compact_if_needed(db, session_id=child_session_id)
                    await account()
                except Exception:
                    pass
            await self._notify_task_completed(task)

    async def _load_task(self, db, task_id):
        return (
            (await db.execute(select(SubAgentTask).where(SubAgentTask.id == task_id)))
            .scalars()
            .first()
        )

    async def _load_parent_session(self, db, session_id):
        return (await db.execute(select(Session).where(Session.id == session_id))).scalars().first()

    async def _mark_failed(self, db, task, reason):
        task.status = "failed"
        task.completed_at = datetime.now(UTC)
        task.result = {**(task.result or {}), "error": reason}
        await db.commit()
        await self._notify_task_completed(task)

    async def _notify_task_completed(self, task):
        if self._on_task_completed:
            result = self._on_task_completed(task)
            if asyncio.iscoroutine(result):
                try:
                    await result
                except Exception:
                    pass

    def _scoped_runtime_support(self, task):
        if self._base_tool_registry is None:
            return self._agent_runtime_support
        allowed = set(
            task.allowed_tools or [tool.name for tool in self._base_tool_registry.list_all()]
        )
        allowed.add("agent_messages")
        registry = ToolRegistry()
        for tool in self._base_tool_registry.list_all():
            if tool.name in allowed and tool.name not in _SUB_AGENT_EXCLUDED_TOOLS:
                registry.register(tool)
        base = self._agent_runtime_support.context_builder
        context = context_builder_module.ContextBuilder(
            instance_name=getattr(base, "_instance_name", None),
            runtime_session_id=task.session_id,
            default_system_prompt=getattr(
                base, "_default_system_prompt", settings.default_system_prompt
            ),
            token_budget=getattr(base, "_token_budget", settings.context_token_budget),
            available_tools={tool.name for tool in registry.list_all()},
            memory_search_service=getattr(base, "_memory_search_service", None),
        )
        return runtime_support_module.SentinelRuntimeSupport(
            self._agent_runtime_support.provider,
            context,
            registry,
            ToolExecutor(
                registry,
                db_session_factory=self._db_factory,
                sub_agent_orchestrator=self,
                approval_waiter=build_tool_db_approval_waiter(session_factory=self._db_factory),
                approval_result_recorder=build_tool_db_approval_result_recorder(
                    session_factory=self._db_factory
                ),
                instance_name=getattr(
                    self._agent_runtime_support.tool_executor, "_instance_name", None
                ),
            ),
        )

    def _sub_agent_system_prompt(self, task):
        return (
            "You are a fully capable delegated peer. Complete the objective within its scope. "
            "Report your findings and any unresolved concerns clearly. Use agent_messages to ask the parent "
            "or another peer a focused question or share relevant findings. You share the parent's workspace; "
            "preserve unrelated work and use your own terminal pane.\n"
            f"Scope: {task.context or 'No additional scope.'}"
        )
