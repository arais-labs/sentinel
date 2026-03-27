"""System module registry — all native tool module definitions."""
from __future__ import annotations

from importlib import import_module

from app.services.araios.module_types import ModuleDefinition

_SYSTEM_MODULE_NAMES = (
    "runtime_exec",
    "python",
    "git_tool",
    "str_replace_editor",
    "http_request",
    "browser",
    "memory",
    "sub_agents",
    "telegram",
    "triggers",
    "module_manager",
    "tasks",
    "documents",
    "coordination",
)


def get_system_modules() -> list[ModuleDefinition]:
    modules: list[ModuleDefinition] = []
    for name in _SYSTEM_MODULE_NAMES:
        package = import_module(f"{__name__}.{name}")
        modules.append(package.MODULE)
    return modules


__all__ = ["get_system_modules"]
