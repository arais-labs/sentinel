from __future__ import annotations

from typing import Any

from app.config import settings
from app.database import AsyncSessionLocal
from app.services.agent import AgentLoop, ContextBuilder, ToolAdapter
from app.services.llm.factory import build_tier_provider_from_settings


class RuntimeRebuildService:
    """Rebuild runtime singletons that depend on mutable settings."""

    def rebuild_agent_loop(self, app_state: Any) -> None:
        provider = build_tier_provider_from_settings(settings)
        app_state.llm_provider = provider
        if provider is None:
            app_state.agent_loop = None
            self._sync_trigger_scheduler_agent_loop(app_state)
            return

        tool_registry = getattr(app_state, "tool_registry", None)
        tool_executor = getattr(app_state, "tool_executor", None)
        memory_search_service = getattr(app_state, "memory_search_service", None)
        if tool_registry is None or tool_executor is None:
            app_state.agent_loop = None
            self._sync_trigger_scheduler_agent_loop(app_state)
            return

        available_tools = {tool.name for tool in tool_registry.list_all()}
        context_builder = ContextBuilder(
            default_system_prompt=settings.default_system_prompt,
            available_tools=available_tools,
            memory_search_service=memory_search_service,
        )
        tool_adapter = ToolAdapter(tool_registry, tool_executor, session_factory=AsyncSessionLocal)
        app_state.agent_loop = AgentLoop(provider, context_builder, tool_adapter)
        self._sync_trigger_scheduler_agent_loop(app_state)

        bridge = getattr(app_state, "telegram_bridge", None)
        if bridge is not None:
            bridge.update_agent_loop(app_state.agent_loop)

    def _sync_trigger_scheduler_agent_loop(self, app_state: Any) -> None:
        scheduler = getattr(app_state, "trigger_scheduler", None)
        if scheduler is None:
            return
        setter = getattr(scheduler, "set_agent_loop", None)
        if callable(setter):
            setter(app_state.agent_loop)
