from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.agent import ContextBuilder, SentinelRuntimeSupport
from app.services.llm.factory import build_tier_provider_from_settings


class RuntimeRebuildService:
    """Rebuild runtime singletons that depend on mutable settings."""

    async def rebuild_request_runtime_support(self, request: Any) -> None:
        from app.services.instance_runtime_context import (
            InstanceRuntimeContext,
            instance_runtime_context_registry,
        )

        context = getattr(request.state, "instance_runtime_context", None)
        if isinstance(context, InstanceRuntimeContext):
            await instance_runtime_context_registry.rebuild_context(
                app_state=request.app.state,
                context=context,
            )
            return
        self.rebuild_agent_runtime_support(request.app.state)

    def rebuild_agent_runtime_support(self, app_state: Any) -> None:
        provider = build_tier_provider_from_settings(settings)
        app_state.llm_provider = provider
        if provider is None:
            app_state.agent_runtime_support = None
            self._sync_trigger_scheduler_runtime_support(app_state)
            return

        tool_registry = getattr(app_state, "tool_registry", None)
        tool_executor = getattr(app_state, "tool_executor", None)
        memory_search_service = getattr(app_state, "memory_search_service", None)
        if tool_registry is None or tool_executor is None:
            app_state.agent_runtime_support = None
            self._sync_trigger_scheduler_runtime_support(app_state)
            return

        available_tools = {tool.name for tool in tool_registry.list_all()}
        context_builder = ContextBuilder(
            default_system_prompt=settings.default_system_prompt,
            available_tools=available_tools,
            memory_search_service=memory_search_service,
        )
        app_state.agent_runtime_support = SentinelRuntimeSupport(
            provider, context_builder, tool_registry, tool_executor,
        )
        self._sync_trigger_scheduler_runtime_support(app_state)

        bridge = getattr(app_state, "telegram_bridge", None)
        if bridge is not None:
            bridge.update_agent_runtime_support(app_state.agent_runtime_support)

    def _sync_trigger_scheduler_runtime_support(self, app_state: Any) -> None:
        scheduler = getattr(app_state, "trigger_scheduler", None)
        if scheduler is None:
            return
        setter = getattr(scheduler, "set_agent_runtime_support", None)
        if callable(setter):
            setter(app_state.agent_runtime_support)
