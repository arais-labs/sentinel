"""Agent runtime package exports with lazy loading.

Avoids importing heavy config-dependent modules at package import time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.agent.context_builder import ContextBuilder
    from app.services.agent.sentinel_runner import AgentLoop, AgentLoopResult
    from app.services.agent.tool_adapter import ToolAdapter

__all__ = ["ContextBuilder", "ToolAdapter", "AgentLoop", "AgentLoopResult"]


def __getattr__(name: str) -> Any:
    if name == "ContextBuilder":
        from app.services.agent.context_builder import ContextBuilder

        return ContextBuilder
    if name == "ToolAdapter":
        from app.services.agent.tool_adapter import ToolAdapter

        return ToolAdapter
    if name == "AgentLoop":
        from app.services.agent.sentinel_runner import AgentLoop

        return AgentLoop
    if name == "AgentLoopResult":
        from app.services.agent.sentinel_runner import AgentLoopResult

        return AgentLoopResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
