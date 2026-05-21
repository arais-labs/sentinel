from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.services.runtime.ssh_client import SSHClient, SSHCredentials
from app.services.runtime.desktop import RuntimeDesktopManager
from app.services.runtime.files import RuntimeWorkspaceFiles
from app.services.runtime.port_forwards import RuntimePortForwardManager
from app.services.runtime.terminal_manager import RuntimeTerminalManager

_manager: RuntimeTerminalManager | None = None
_files: RuntimeWorkspaceFiles | None = None
_forwards: RuntimePortForwardManager | None = None
_desktop: RuntimeDesktopManager | None = None


def runtime_configured() -> bool:
    return bool(settings.runtime_ssh_host.strip() and settings.runtime_ssh_username.strip())


def get_runtime_terminal_manager() -> RuntimeTerminalManager:
    global _manager
    if _manager is not None:
        return _manager
    host = settings.runtime_ssh_host.strip()
    username = settings.runtime_ssh_username.strip()
    if not host or not username:
        raise RuntimeError(
            "Runtime SSH is not configured. Set SENTINEL_RUNTIME_SSH_HOST and "
            "SENTINEL_RUNTIME_SSH_USERNAME."
        )
    key_path_raw = settings.runtime_ssh_key_path.strip()
    password = settings.runtime_ssh_password or None
    credentials = SSHCredentials(
        host=host,
        port=int(settings.runtime_ssh_port),
        username=username,
        key_path=Path(key_path_raw).expanduser() if key_path_raw else None,
        password=password,
    )
    _manager = RuntimeTerminalManager(
        SSHClient(credentials),
        workspaces_root=settings.runtime_workspaces_dir,
    )
    return _manager


def get_runtime_workspace_files() -> RuntimeWorkspaceFiles:
    global _files
    if _files is not None:
        return _files
    manager = get_runtime_terminal_manager()
    _files = RuntimeWorkspaceFiles(manager.ssh, workspaces_root=settings.runtime_workspaces_dir)
    return _files


def get_runtime_port_forward_manager() -> RuntimePortForwardManager:
    global _forwards
    if _forwards is not None:
        return _forwards
    manager = get_runtime_terminal_manager()
    _forwards = RuntimePortForwardManager(manager.ssh)
    return _forwards


def get_runtime_desktop_manager() -> RuntimeDesktopManager:
    global _desktop
    if _desktop is not None:
        return _desktop
    manager = get_runtime_terminal_manager()
    _desktop = RuntimeDesktopManager(manager, workspaces_root=settings.runtime_workspaces_dir)
    return _desktop


async def close_runtime_terminal_manager() -> None:
    global _manager, _files, _forwards, _desktop
    if _desktop is not None:
        await _desktop.close_all()
        _desktop = None
    if _forwards is not None:
        await _forwards.close_all()
        _forwards = None
    if _manager is not None:
        await _manager.close()
        _manager = None
    _files = None
