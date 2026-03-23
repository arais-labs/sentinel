"""Builds the ToolRegistry from system modules."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.araios.system_modules import get_system_modules
from app.services.tools.registry import ToolRegistry


def build_default_registry(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ToolRegistry:
    """Build the tool registry from all system modules."""
    registry = ToolRegistry()

    for module in get_system_modules():
        for tool_def in module.to_tool_definitions(session_factory=session_factory):
            registry.register(tool_def)

    return registry
