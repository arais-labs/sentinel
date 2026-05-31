from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from uuid import UUID

from app.services.runtime.environment import RuntimeEnvironment, detect_runtime_environment
from app.services.runtime.remote_commands import load_remote_command
from app.services.runtime.ssh_client import SSHClient
from app.services.runtime.workspace import workspace_paths


class RuntimePathInvalidError(ValueError):
    pass


class RuntimePathNotFoundError(FileNotFoundError):
    pass


class RuntimePathIsDirectoryError(IsADirectoryError):
    pass


class RuntimeSandboxUnavailableError(RuntimePathInvalidError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimeDownload:
    content: bytes
    download_name: str
    media_type: str


class RuntimeWorkspaceFiles:
    def __init__(self, ssh: SSHClient, *, workspaces_root: str | None = None) -> None:
        self._ssh = ssh
        self._workspaces_root = workspaces_root
        self._environment: RuntimeEnvironment | None = None

    async def list_files(self, session_id: UUID | str, *, path: str = "", limit: int = 400) -> dict:
        return await self._run_json(
            session_id,
            "list_files",
            {"path": path, "limit": limit},
            timeout=20,
        )

    async def preview_file(
        self,
        session_id: UUID | str,
        *,
        path: str,
        max_bytes: int = 32_000,
    ) -> dict:
        return await self._run_json(
            session_id,
            "preview_file",
            {"path": path, "max_bytes": max_bytes},
            timeout=20,
        )

    async def download(self, session_id: UUID | str, *, path: str) -> RuntimeDownload:
        payload = await self._run_json(
            session_id,
            "download",
            {"path": path},
            timeout=120,
        )
        encoded = str(payload.get("content_base64") or "")
        return RuntimeDownload(
            content=base64.b64decode(encoded.encode("ascii")),
            download_name=str(payload.get("download_name") or "download"),
            media_type=str(payload.get("media_type") or "application/octet-stream"),
        )

    async def git_roots(self, session_id: UUID | str, *, path: str = "", limit: int = 200) -> dict:
        return await self._run_json(
            session_id,
            "git_roots",
            {"path": path, "limit": limit},
            timeout=20,
        )

    async def git_changed(
        self, session_id: UUID | str, *, path: str = "", limit: int = 200
    ) -> dict:
        return await self._run_json(
            session_id,
            "git_changed",
            {"path": path, "limit": limit},
            timeout=20,
        )

    async def git_diff(
        self,
        session_id: UUID | str,
        *,
        path: str,
        base_ref: str = "HEAD",
        staged: bool = False,
        context_lines: int = 3,
        max_bytes: int = 120_000,
    ) -> dict:
        return await self._run_json(
            session_id,
            "git_diff",
            {
                "path": path,
                "base_ref": base_ref,
                "staged": staged,
                "context_lines": context_lines,
                "max_bytes": max_bytes,
            },
            timeout=30,
        )

    async def str_replace(
        self,
        session_id: UUID | str,
        *,
        path: str,
        old_str: str,
        new_str: str,
    ) -> dict:
        return await self._run_json(
            session_id,
            "str_replace",
            {"path": path, "old_str": old_str, "new_str": new_str},
            timeout=30,
        )

    async def _run_json(
        self,
        session_id: UUID | str,
        operation: str,
        payload: dict,
        *,
        timeout: int,
    ) -> dict:
        paths = workspace_paths(str(session_id), root=self._workspaces_root)
        await self._require_supported_environment()
        request = {
            "operation": operation,
            "session_id": paths.session_id,
            "session_root": paths.session_root,
            "workspace": paths.workspace,
            "payload": payload,
        }
        result = await self._ssh.run_script(
            load_remote_command("common/workspace/files.sh"),
            args=[json.dumps(request, separators=(",", ":"))],
            timeout=timeout,
        )
        if result.exit_status not in {0, None}:
            detail = (result.stderr or result.stdout or "").strip()[:500]
            _raise_remote_error("runtime_error", detail)
        try:
            response = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimePathInvalidError("Runtime file response was not valid JSON") from exc
        if not isinstance(response, dict):
            raise RuntimePathInvalidError("Runtime file response was not an object")
        if response.get("ok") is False:
            _raise_remote_error(
                str(response.get("error") or "runtime_error"), str(response.get("detail") or "")
            )
        data = response.get("data")
        return data if isinstance(data, dict) else {}

    async def _require_supported_environment(self) -> RuntimeEnvironment:
        environment = self._environment
        if environment is None:
            environment = await detect_runtime_environment(self._ssh)
            self._environment = environment
        if not environment.supported:
            raise RuntimeSandboxUnavailableError(
                "Runtime must be Linux with bubblewrap or macOS with sandbox-exec "
                f"(detected os={environment.os}, sandbox={environment.sandbox})."
            )
        return environment


def _raise_remote_error(error: str, detail: str) -> None:
    if error == "not_found":
        raise RuntimePathNotFoundError(detail or "Runtime path not found")
    if error == "is_directory":
        raise RuntimePathIsDirectoryError(detail or "Runtime path is a directory")
    raise RuntimePathInvalidError(detail or "Invalid runtime path")
