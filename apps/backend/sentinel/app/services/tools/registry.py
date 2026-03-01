from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


ToolExecutorFn = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    risk_level: str
    parameters_schema: dict[str, Any]
    execute: ToolExecutorFn
    enabled: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_all(self) -> list[ToolDefinition]:
        return sorted(self._tools.values(), key=lambda item: item.name)

    def is_allowed(self, name: str) -> bool:
        tool = self.get(name)
        return bool(tool and tool.enabled)
