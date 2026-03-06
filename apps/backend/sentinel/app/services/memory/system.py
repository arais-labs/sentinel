from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SystemMemorySpec:
    key: str
    title: str
    importance: int


SYSTEM_MEMORY_SPECS: tuple[SystemMemorySpec, ...] = (
    SystemMemorySpec(key="agent_identity", title="Agent Identity", importance=100),
    SystemMemorySpec(key="user_profile", title="User Profile", importance=90),
)

SYSTEM_MEMORY_KEYS: set[str] = {item.key for item in SYSTEM_MEMORY_SPECS}
SYSTEM_MEMORY_TITLES: set[str] = {item.title for item in SYSTEM_MEMORY_SPECS}


def is_system_memory_key(value: str | None) -> bool:
    normalized = (value or "").strip()
    return normalized in SYSTEM_MEMORY_KEYS
