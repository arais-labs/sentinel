from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, settings
from app.models.manager import SentinelInstance
from app.services.agent import ContextBuilder, SentinelRuntimeSupport
from app.services.llm.factory import build_tier_provider_from_settings
from app.services.memory.backfill import run_memory_embedding_backfill
from app.services.memory.embeddings import EmbeddingService
from app.services.settings.settings_service import SettingsService
from app.services.sub_agents import SubAgentOrchestrator
from app.services.tools import ToolExecutor, ToolRegistry
from app.services.tools.approval.approval_waiters import (
    build_tool_db_approval_result_recorder,
    build_tool_db_approval_waiter,
)
from app.services.tools.runtime_registry import build_runtime_registry
from app.services.triggers.trigger_scheduler import TriggerScheduler

if TYPE_CHECKING:
    from app.services.telegram.bridge import TelegramBridge


@dataclass(slots=True)
class _InstanceRuntimeIdentity:
    name: str
    database_name: str


@dataclass(slots=True)
class InstanceRuntimeContext:
    name: str
    database_name: str
    instance_settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    tool_registry: ToolRegistry
    tool_executor: ToolExecutor
    agent_runtime_support: SentinelRuntimeSupport | None
    trigger_scheduler: TriggerScheduler
    sub_agent_orchestrator: SubAgentOrchestrator
    background_tasks: list[asyncio.Task[Any]]
    telegram_bridge: "TelegramBridge | None" = None


class InstanceRuntimeContextRegistry:
    def __init__(self) -> None:
        self._contexts: dict[str, InstanceRuntimeContext] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self,
        *,
        app_state: Any,
        instance: SentinelInstance,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> InstanceRuntimeContext:
        key = _normalize_context_name(instance.name)
        async with self._lock:
            existing = self._contexts.get(key)
            if existing is not None and existing.database_name == instance.database_name:
                return existing

            context = await _build_instance_runtime_context(
                app_state=app_state,
                instance=_InstanceRuntimeIdentity(name=key, database_name=instance.database_name),
                session_factory=session_factory,
            )
            self._contexts[key] = context
            return context

    async def rebuild(
        self,
        *,
        app_state: Any,
        instance: SentinelInstance,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> InstanceRuntimeContext:
        key = _normalize_context_name(instance.name)
        async with self._lock:
            old = self._contexts.pop(key, None)
            if old is not None:
                await _stop_instance_context(old)
            context = await _build_instance_runtime_context(
                app_state=app_state,
                instance=_InstanceRuntimeIdentity(name=key, database_name=instance.database_name),
                session_factory=session_factory,
            )
            self._contexts[key] = context
            return context

    async def rebuild_context(
        self,
        *,
        app_state: Any,
        context: InstanceRuntimeContext,
    ) -> InstanceRuntimeContext:
        key = _normalize_context_name(context.name)
        async with self._lock:
            old = self._contexts.pop(key, None)
            if old is not None:
                await _stop_instance_context(old)
            rebuilt = await _build_instance_runtime_context(
                app_state=app_state,
                instance=_InstanceRuntimeIdentity(
                    name=key,
                    database_name=context.database_name,
                ),
                session_factory=context.session_factory,
            )
            self._contexts[key] = rebuilt
            return rebuilt

    def get(self, instance_name: str) -> InstanceRuntimeContext | None:
        return self._contexts.get(_normalize_context_name(instance_name))

    def find_by_session_factory(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> InstanceRuntimeContext | None:
        for context in self._contexts.values():
            if context.session_factory is session_factory:
                return context
        return None

    def all(self) -> list[InstanceRuntimeContext]:
        return list(self._contexts.values())

    def telegram_token_in_use_by_other(
        self, token: str, *, exclude_name: str | None = None
    ) -> bool:
        """True when another instance's runtime context already owns this bot token."""
        excluded = _normalize_context_name(exclude_name) if exclude_name else None
        for context in self._contexts.values():
            if excluded is not None and context.name == excluded:
                continue
            if context.instance_settings.telegram_bot_token == token:
                return True
        return False

    async def remove(self, instance_name: str) -> None:
        key = _normalize_context_name(instance_name)
        async with self._lock:
            context = self._contexts.pop(key, None)
        if context is not None:
            await _stop_instance_context(context)

    async def stop_all(self) -> None:
        async with self._lock:
            contexts = list(self._contexts.values())
            self._contexts.clear()
        for context in contexts:
            await _stop_instance_context(context)


async def _build_instance_runtime_context(
    *,
    app_state: Any,
    instance: SentinelInstance | _InstanceRuntimeIdentity,
    session_factory: async_sessionmaker[AsyncSession],
) -> InstanceRuntimeContext:
    async with session_factory() as db:
        instance_settings = await SettingsService().build_instance_settings(db)

    tool_registry = await build_runtime_registry(session_factory=session_factory)
    tool_executor = ToolExecutor(
        tool_registry,
        approval_waiter=build_tool_db_approval_waiter(session_factory=session_factory),
        approval_result_recorder=build_tool_db_approval_result_recorder(
            session_factory=session_factory
        ),
        db_session_factory=session_factory,
        instance_name=instance.name,
    )

    provider = build_tier_provider_from_settings(instance_settings)
    memory_search_service = getattr(app_state, "memory_search_service", None)
    agent_runtime_support = None
    if provider is not None:
        available_tools = {tool.name for tool in tool_registry.list_all()}
        context_builder = ContextBuilder(
            default_system_prompt=instance_settings.default_system_prompt,
            available_tools=available_tools,
            memory_search_service=memory_search_service,
        )
        agent_runtime_support = SentinelRuntimeSupport(
            provider,
            context_builder,
            tool_registry,
            tool_executor,
        )

    scheduler = TriggerScheduler(
        agent_runtime_support=agent_runtime_support,
        tool_executor=tool_executor,
        ws_manager=getattr(app_state, "ws_manager", None),
        run_registry=getattr(app_state, "agent_run_registry", None),
        db_factory=session_factory,
    )
    sub_agent_orchestrator = SubAgentOrchestrator(
        agent_runtime_support=agent_runtime_support,
        db_factory=session_factory,
        base_tool_registry=tool_registry,
        on_task_completed=getattr(app_state, "sub_agent_completed_callback", None),
    )
    tool_executor.set_runtime_defaults(
        sub_agent_orchestrator=sub_agent_orchestrator, instance_name=instance.name
    )

    telegram_bridge = None
    _tg_token = getattr(instance_settings, "telegram_bot_token", None)
    if _tg_token:
        from app.services.telegram.bridge import TelegramBridge

        telegram_bridge = TelegramBridge(
            bot_token=_tg_token,
            user_id=instance_settings.telegram_owner_user_id or instance_settings.dev_user_id,
            agent_runtime_support=agent_runtime_support,
            run_registry=getattr(app_state, "agent_run_registry", None),
            ws_manager=getattr(app_state, "ws_manager", None),
            db_factory=session_factory,
            instance_settings=instance_settings,
        )

    tasks: list[asyncio.Task[Any]] = []
    stop_event = getattr(app_state, "instance_stop_event", None)
    if isinstance(stop_event, asyncio.Event):
        tasks.append(asyncio.create_task(scheduler.start(stop_event)))
        if telegram_bridge is not None:
            tasks.append(asyncio.create_task(telegram_bridge.start(stop_event)))
        embedding_service = getattr(app_state, "embedding_service", None)
        if (
            isinstance(embedding_service, EmbeddingService)
            and instance_settings.memory_embedding_backfill_on_start
        ):
            tasks.append(
                asyncio.create_task(
                    run_memory_embedding_backfill(
                        stop_event=stop_event,
                        db_factory=session_factory,
                        embedding_service=embedding_service,
                        batch_size=instance_settings.memory_embedding_backfill_batch_size,
                        max_rows=instance_settings.memory_embedding_backfill_max_rows,
                    )
                )
            )

    return InstanceRuntimeContext(
        name=instance.name,
        database_name=instance.database_name,
        instance_settings=instance_settings,
        session_factory=session_factory,
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        agent_runtime_support=agent_runtime_support,
        trigger_scheduler=scheduler,
        sub_agent_orchestrator=sub_agent_orchestrator,
        background_tasks=tasks,
        telegram_bridge=telegram_bridge,
    )


async def _stop_instance_context(context: InstanceRuntimeContext) -> None:
    for task in context.background_tasks:
        task.cancel()
    if context.background_tasks:
        await asyncio.gather(*context.background_tasks, return_exceptions=True)
    if context.telegram_bridge is not None:
        with contextlib.suppress(Exception):
            await context.telegram_bridge.stop()


def _normalize_context_name(instance_name: str) -> str:
    from app.services.instances import InvalidInstanceNameError, normalize_instance_name

    try:
        return normalize_instance_name(instance_name)
    except InvalidInstanceNameError:
        return instance_name


instance_runtime_context_registry = InstanceRuntimeContextRegistry()
