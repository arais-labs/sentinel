"""Pluggable runtime providers for session command execution.

Backends:
    docker  — launches a Docker container per session (default)
    remote  — connects to a pre-existing machine via SSH
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.runtime.docker import DockerRuntimeProvider
    from app.services.runtime.provider import SSHRuntimeProvider

_docker_provider: DockerRuntimeProvider | None = None
_ssh_provider: SSHRuntimeProvider | None = None


def get_docker_runtime() -> DockerRuntimeProvider:
    global _docker_provider
    if _docker_provider is None:
        from app.services.runtime.docker import DockerRuntimeProvider
        _docker_provider = DockerRuntimeProvider()
    return _docker_provider


def get_ssh_runtime() -> SSHRuntimeProvider:
    global _ssh_provider
    if _ssh_provider is None:
        from app.services.runtime.provider import SSHRuntimeProvider
        _ssh_provider = SSHRuntimeProvider()
    return _ssh_provider


def get_runtime():
    """Return the configured runtime provider."""
    from app.config import settings
    if settings.runtime_exec_backend == "docker":
        return get_docker_runtime()
    if settings.runtime_exec_backend == "remote":
        return get_ssh_runtime()
    raise ValueError(
        f"Unknown runtime backend={settings.runtime_exec_backend!r}. "
        f"Valid: 'docker', 'remote'"
    )
