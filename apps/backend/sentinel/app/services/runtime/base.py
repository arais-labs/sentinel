from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID


@dataclass(slots=True)
class RuntimeExecResult:
    exit_status: int | None
    stdout: str
    stderr: str


@dataclass(slots=True)
class RuntimeProviderInfoItem:
    key: str
    label: str
    value: str


@dataclass(slots=True)
class RuntimeProviderInfo:
    id: str
    label: str
    status: str | None = None
    summary: str | None = None
    items: list[RuntimeProviderInfoItem] = field(default_factory=list)


class RuntimeCommandClient(Protocol):
    async def wait_ready(self, *, timeout: int = 60) -> None: ...

    async def run(
        self,
        command: str,
        *,
        timeout: int = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> RuntimeExecResult: ...

    async def run_detached(
        self,
        command: str,
        *,
        stdout_path: str,
        stderr_path: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> int: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class RuntimeInstance:
    session_id: str
    client: RuntimeCommandClient
    workspace_path: str
    host: str
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeProvider(Protocol):
    async def ensure(self, session_id: UUID | str) -> RuntimeInstance: ...

    async def activate_session(self, session_id: UUID | str) -> RuntimeInstance: ...

    async def describe(self, session_id: UUID | str) -> RuntimeProviderInfo: ...

    async def hard_restart(self, session_id: UUID | str) -> RuntimeInstance: ...

    async def destroy(self, session_id: UUID | str) -> None: ...

    async def stop(self, session_id: UUID | str) -> bool: ...

    async def stop_all(self) -> int: ...

    def get(self, session_id: UUID | str) -> RuntimeInstance | None: ...

    async def recover_existing(self) -> int: ...

    def get_host(self, session_id: UUID | str) -> str | None: ...

    def get_public_host(self, session_id: UUID | str) -> str | None: ...

    def resolve_port(self, session_id: UUID | str, internal_port: int) -> int | None: ...

    async def restart_browser(self, session_id: UUID | str, runtime: RuntimeInstance) -> None: ...
