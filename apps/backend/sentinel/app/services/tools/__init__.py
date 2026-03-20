from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.tools.browser_pool import BrowserPool
    from app.services.tools.browser_tool import BrowserManager
    from app.services.tools.executor import ToolExecutor
    from app.services.tools.registry import ToolDefinition, ToolRegistry

__all__ = [
    "BrowserManager",
    "BrowserPool",
    "ToolDefinition",
    "ToolExecutor",
    "ToolRegistry",
    "build_default_registry",
]


def __getattr__(name: str) -> Any:
    if name == "BrowserPool":
        from app.services.tools.browser_pool import BrowserPool

        return BrowserPool
    if name == "BrowserManager":
        from app.services.tools.browser_tool import BrowserManager

        return BrowserManager
    if name == "ToolExecutor":
        from app.services.tools.executor import ToolExecutor

        return ToolExecutor
    if name in {"ToolDefinition", "ToolRegistry"}:
        from app.services.tools.registry import ToolDefinition, ToolRegistry

        return {"ToolDefinition": ToolDefinition, "ToolRegistry": ToolRegistry}[name]
    if name == "build_default_registry":
        from app.services.tools.builtin import build_default_registry

        return build_default_registry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
