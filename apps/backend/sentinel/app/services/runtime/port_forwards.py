from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.services.runtime.ssh_client import SSHClient


class RuntimeForwardError(RuntimeError):
    pass


class RuntimeForwardNotFound(RuntimeForwardError):
    pass


@dataclass(slots=True)
class RuntimeForward:
    forward_id: str
    session_id: str
    target_host: str
    target_port: int
    protocol: str
    label: str | None
    local_host: str
    local_port: int
    listener: Any
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: str = "open"
    closed_at: datetime | None = None
    error: str | None = None

    @property
    def proxy_path(self) -> str:
        return f"/sessions/{self.session_id}/runtime/forwards/{self.forward_id}/"

    @property
    def default_instance_proxy_path(self) -> str:
        return f"/api/v1/instances/main{self.proxy_path}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "forward_id": self.forward_id,
            "session_id": self.session_id,
            "status": self.status,
            "proxy_path": self.proxy_path,
            "proxy_url": self.default_instance_proxy_path,
            "url": self.default_instance_proxy_path,
            "target_host": self.target_host,
            "target_port": self.target_port,
            "protocol": self.protocol,
            "label": self.label,
            "created_at": self.created_at.isoformat(),
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "error": self.error,
        }


def normalize_forward_host(value: str | None) -> str:
    host = (value or "127.0.0.1").strip().lower()
    if host == "localhost":
        return "127.0.0.1"
    if host != "127.0.0.1":
        raise RuntimeForwardError("Only loopback runtimes are supported in v1.")
    return host


def normalize_forward_protocol(value: str | None) -> str:
    protocol = (value or "http").strip().lower()
    if protocol in {"ws", "websocket", "websockets"}:
        return "websocket"
    if protocol == "http":
        return "http"
    raise RuntimeForwardError("Field 'protocol' must be http or websocket.")


def validate_forward_port(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
        raise RuntimeForwardError("Field 'port' must be an integer between 1 and 65535.")
    return value


def _allocate_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class RuntimePortForwardManager:
    def __init__(self, ssh: SSHClient) -> None:
        self._ssh = ssh
        self._forwards: dict[str, RuntimeForward] = {}
        self._lock = asyncio.Lock()

    async def open_forward(
        self,
        *,
        session_id: str,
        target_host: str,
        target_port: int,
        protocol: str = "http",
        label: str | None = None,
    ) -> RuntimeForward:
        target_host = normalize_forward_host(target_host)
        target_port = validate_forward_port(target_port)
        protocol = normalize_forward_protocol(protocol)
        normalized_label = label.strip() if isinstance(label, str) and label.strip() else None
        async with self._lock:
            existing = self._find_existing_locked(
                session_id=session_id,
                target_host=target_host,
                target_port=target_port,
                protocol=protocol,
            )
            if existing is not None:
                return existing

            local_port = _allocate_local_port()
            try:
                listener = await self._ssh.forward_local_port(
                    "127.0.0.1",
                    local_port,
                    target_host,
                    target_port,
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeForwardError(f"Failed to open SSH forward: {exc}") from exc
            forward = RuntimeForward(
                forward_id=f"pf-{uuid4().hex[:12]}",
                session_id=session_id,
                target_host=target_host,
                target_port=target_port,
                protocol=protocol,
                label=normalized_label,
                local_host="127.0.0.1",
                local_port=local_port,
                listener=listener,
            )
            self._forwards[forward.forward_id] = forward
            return forward

    async def list_forwards(self, *, session_id: str) -> list[RuntimeForward]:
        async with self._lock:
            return [
                item
                for item in self._forwards.values()
                if item.session_id == session_id and item.status == "open"
            ]

    async def get_forward(self, *, session_id: str, forward_id: str) -> RuntimeForward:
        async with self._lock:
            forward = self._forwards.get(forward_id)
            if forward is None or forward.session_id != session_id or forward.status != "open":
                raise RuntimeForwardNotFound("Runtime forward not found.")
            return forward

    async def close_forward(self, *, session_id: str, forward_id: str) -> RuntimeForward:
        async with self._lock:
            forward = self._forwards.get(forward_id)
            if forward is None or forward.session_id != session_id:
                raise RuntimeForwardNotFound("Runtime forward not found.")
            await self._close_locked(forward)
            self._forwards.pop(forward.forward_id, None)
            return forward

    async def close_session(self, session_id: str) -> None:
        async with self._lock:
            forwards = [item for item in self._forwards.values() if item.session_id == session_id]
            for forward in forwards:
                await self._close_locked(forward)
                self._forwards.pop(forward.forward_id, None)

    async def close_all(self) -> None:
        async with self._lock:
            forwards = list(self._forwards.values())
            for forward in forwards:
                await self._close_locked(forward)
            self._forwards.clear()

    def _find_existing_locked(
        self,
        *,
        session_id: str,
        target_host: str,
        target_port: int,
        protocol: str,
    ) -> RuntimeForward | None:
        for forward in self._forwards.values():
            if (
                forward.session_id == session_id
                and forward.target_host == target_host
                and forward.target_port == target_port
                and forward.protocol == protocol
                and forward.status == "open"
            ):
                return forward
        return None

    async def _close_locked(self, forward: RuntimeForward) -> None:
        if forward.status == "closed":
            return
        try:
            forward.listener.close()
            wait_closed = getattr(forward.listener, "wait_closed", None)
            if callable(wait_closed):
                maybe_coro = wait_closed()
                if asyncio.iscoroutine(maybe_coro):
                    await maybe_coro
        except Exception as exc:  # noqa: BLE001
            forward.error = str(exc)
        finally:
            forward.status = "closed"
            forward.closed_at = datetime.now(UTC)
