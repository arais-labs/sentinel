"""Builds the ToolRegistry from system modules."""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.araios.system_modules import get_system_modules
from app.services.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def build_default_registry(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ToolRegistry:
    """Build the tool registry from all system modules."""
    registry = ToolRegistry()

    for module in get_system_modules():
        try:
            tool_defs = module.to_tool_definitions(session_factory=session_factory)
        except Exception:
            logger.exception("tool_registry_skip_system_module module=%s", module.name)
            continue
        for tool_def in tool_defs:
            registry.register(tool_def)

    return registry
