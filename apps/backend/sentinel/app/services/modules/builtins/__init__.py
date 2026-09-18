"""Built-in module registry — all native tool module definitions."""

from __future__ import annotations

from importlib import import_module

from app.services.modules.definitions import ModuleDefinition

_BUILTIN_MODULE_NAMES = (
    "http_request",
    "browser",
    "computer",
    "runtime",
    "host_runtime",
    "session_layout",
    "port_forward",
    "git_tool",
    "str_replace_editor",
    "memory",
    "sub_agents",
    "telegram",
    "triggers",
    "module_manager",
    "tasks",
    "documents",
    "chats",
    "conversation_history",
    "form",
    "notification",
    "catalog",
)


def is_retired_module(module) -> bool:
    # Preserve historical records in storage/backups without exposing the retired channel.
    return bool(module.system and module.name == "coordination")


def get_builtins() -> list[ModuleDefinition]:
    modules: list[ModuleDefinition] = []
    for name in _BUILTIN_MODULE_NAMES:
        package = import_module(f"{__name__}.{name}")
        modules.append(package.MODULE)
    return modules


__all__ = ["get_builtins"]
