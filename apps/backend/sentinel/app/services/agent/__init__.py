"""Agent runtime package exports with lazy loading.

Avoids importing heavy config-dependent modules at package import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.agent.context_builder import ContextBuilder
    from app.services.agent.runtime_support import PreparedRuntimeTurnContext, SentinelRuntimeSupport
    from app.services.agent.tool_adapter import ToolAdapter

__all__ = ["ContextBuilder", "ToolAdapter", "SentinelRuntimeSupport", "PreparedRuntimeTurnContext"]


def __getattr__(name: str) -> Any:
    if name == "ContextBuilder":
        from app.services.agent.context_builder import ContextBuilder

        return ContextBuilder
    if name == "ToolAdapter":
        from app.services.agent.tool_adapter import ToolAdapter

        return ToolAdapter
    if name == "SentinelRuntimeSupport":
        from app.services.agent.runtime_support import SentinelRuntimeSupport

        return SentinelRuntimeSupport
    if name == "PreparedRuntimeTurnContext":
        from app.services.agent.runtime_support import PreparedRuntimeTurnContext

        return PreparedRuntimeTurnContext
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
