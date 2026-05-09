"""Pluggable runtime providers for session command execution.

Backends:
    docker  — launches a Docker container per session (default)
    multipass — launches a Multipass VM per session
    qemu — runs sessions inside one shared local QEMU VM
    remote  — connects to a pre-existing machine via SSH
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.runtime.base import RuntimeProvider
    from app.services.runtime.docker import DockerRuntimeProvider
    from app.services.runtime.multipass import MultipassRuntimeProvider
    from app.services.runtime.qemu import QemuRuntimeProvider
    from app.services.runtime.provider import RemoteRuntimeProvider

_docker_provider: DockerRuntimeProvider | None = None
_multipass_provider: MultipassRuntimeProvider | None = None
_qemu_provider: QemuRuntimeProvider | None = None
_remote_provider: RemoteRuntimeProvider | None = None


def get_docker_runtime() -> DockerRuntimeProvider:
    global _docker_provider
    if _docker_provider is None:
        from app.services.runtime.docker import DockerRuntimeProvider
        _docker_provider = DockerRuntimeProvider()
    return _docker_provider


def get_multipass_runtime() -> MultipassRuntimeProvider:
    global _multipass_provider
    if _multipass_provider is None:
        from app.services.runtime.multipass import MultipassRuntimeProvider
        _multipass_provider = MultipassRuntimeProvider()
    return _multipass_provider


def get_qemu_runtime() -> QemuRuntimeProvider:
    global _qemu_provider
    if _qemu_provider is None:
        from app.services.runtime.qemu import QemuRuntimeProvider
        _qemu_provider = QemuRuntimeProvider()
    return _qemu_provider


def get_remote_runtime() -> RemoteRuntimeProvider:
    global _remote_provider
    if _remote_provider is None:
        from app.services.runtime.provider import RemoteRuntimeProvider
        _remote_provider = RemoteRuntimeProvider()
    return _remote_provider


def get_runtime() -> RuntimeProvider:
    """Return the configured runtime provider."""
    from app.config import settings
    if settings.runtime_exec_backend == "docker":
        return get_docker_runtime()
    if settings.runtime_exec_backend == "multipass":
        return get_multipass_runtime()
    if settings.runtime_exec_backend == "qemu":
        return get_qemu_runtime()
    if settings.runtime_exec_backend == "remote":
        return get_remote_runtime()
    raise ValueError(
        f"Unknown runtime backend={settings.runtime_exec_backend!r}. "
        f"Valid: 'docker', 'multipass', 'qemu', 'remote'"
    )
