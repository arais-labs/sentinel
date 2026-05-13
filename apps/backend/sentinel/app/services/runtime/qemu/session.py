from __future__ import annotations

import os

from app.services.runtime.base import RuntimeExecResult
from app.services.runtime.qemu.profile import QemuProfile
from app.services.runtime.ssh_client import SSHClient

DEFAULT_SESSION_ROOT = "/srv/sentinel/sessions"


def quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def session_guest_workspace(session_id: str) -> str:
    return f"{DEFAULT_SESSION_ROOT}/{session_id}/workspace"


def session_guest_profile(session_id: str) -> str:
    return f"{DEFAULT_SESSION_ROOT}/{session_id}/browser-profile"


def session_guest_runtime_dir(session_id: str) -> str:
    return f"{DEFAULT_SESSION_ROOT}/{session_id}/runtime"


def session_guest_venv_root(session_id: str) -> str:
    return f"{DEFAULT_SESSION_ROOT}/{session_id}/venvs"


def session_host_workspace(profile: QemuProfile, session_id: str) -> str:
    return os.path.join(profile.workspace_root, session_id, "workspace")


def session_share_source(profile: QemuProfile, session_id: str) -> str:
    return f"{profile.share_mount.rstrip('/')}/{session_id}/workspace"


class QemuSessionClient:
    def __init__(
        self,
        *,
        ssh: SSHClient,
        session_user: str,
        workspace_path: str,
    ) -> None:
        self._ssh = ssh
        self._session_user = session_user
        self._workspace_path = workspace_path

    async def wait_ready(self, *, timeout: int = 60) -> None:
        _ = timeout
        return None

    async def run(
        self,
        command: str,
        *,
        timeout: int = 300,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> RuntimeExecResult:
        if as_root:
            return await self._ssh.run(
                command,
                timeout=timeout,
                cwd=cwd or self._workspace_path,
                env=env,
                as_root=True,
            )

        wrapped = f"sudo -u {self._session_user} bash -lc {quote(self._build_session_script(command, cwd=cwd, env=env))}"
        return await self._ssh.run(wrapped, timeout=timeout)

    async def run_detached(
        self,
        command: str,
        *,
        stdout_path: str,
        stderr_path: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        as_root: bool = False,
    ) -> int:
        if as_root:
            return await self._ssh.run_detached(
                command,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                cwd=cwd or self._workspace_path,
                env=env,
                as_root=True,
            )

        return await self._ssh.run_detached_script(
            self._build_session_script(command, cwd=cwd, env=env),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            shell_prefix=f"sudo -u {self._session_user} bash -lc",
        )

    async def close(self) -> None:
        return None

    def _build_session_script(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        prefix: list[str] = []
        if env:
            for key, value in env.items():
                prefix.append(f"export {key}={quote(value)};")
        target_cwd = cwd or self._workspace_path
        prefix.append(f"cd {quote(target_cwd)} &&")
        prefix.append(command)
        return " ".join(prefix)
