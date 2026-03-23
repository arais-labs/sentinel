"""System module registry — all native tool module definitions."""
from __future__ import annotations

from app.services.araios.module_types import ModuleDefinition

from . import (
    browser,
    coordination,
    documents,
    git_exec,
    http_request,
    memory,
    modules_discovery,
    python,
    runtime_exec,
    str_replace_editor,
    sub_agents,
    tasks,
    telegram,
    triggers,
)

_ALL_MODULES = [
    runtime_exec,
    python,
    git_exec,
    str_replace_editor,
    http_request,
    browser,
    memory,
    sub_agents,
    telegram,
    triggers,
    modules_discovery,
    tasks,
    documents,
    coordination,
]

SYSTEM_MODULES: list[ModuleDefinition] = [m.MODULE for m in _ALL_MODULES]
