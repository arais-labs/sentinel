from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.araios.dynamic_modules import load_dynamic_module_tool_definitions
from app.services.runtime.runtime_rebuild import RuntimeRebuildService
from app.services.tools.approval.approval_waiters import (
    build_tool_db_approval_result_recorder,
    build_tool_db_approval_waiter,
)
from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry
from app.services.tools.registry_builder import build_default_registry


async def build_runtime_registry(
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolRegistry:
    registry = build_default_registry(session_factory=session_factory)
    for tool_def in await load_dynamic_module_tool_definitions(session_factory=session_factory):
        registry.register(tool_def)
    return registry


async def rebuild_runtime_registry(
    *,
    app_state: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolRegistry:
    registry = await build_runtime_registry(session_factory)
    app_state.tool_registry = registry
    app_state.tool_executor = ToolExecutor(
        registry,
        approval_waiter=build_tool_db_approval_waiter(session_factory=session_factory),
        approval_result_recorder=build_tool_db_approval_result_recorder(session_factory=session_factory),
    )
    RuntimeRebuildService().rebuild_agent_loop(app_state)
    return registry
