from __future__ import annotations

from app.services.runtime.workspace import WorkspaceLocation

import json
from types import SimpleNamespace
from uuid import UUID

from app.services.runtime.environment import RuntimeEnvironment, detect_runtime_environment
from app.services.runtime.guest_commands import guest_python_command
import app.services.runtime.file_stream as file_stream
import app.services.runtime.container_transport as container_transport
from app.services.runtime.local_transport import RuntimeTransport
from app.services.runtime.workspace import workspace_paths


class RuntimePathInvalidError(ValueError):
    pass


class RuntimePathNotFoundError(FileNotFoundError):
    pass


class RuntimePathIsDirectoryError(IsADirectoryError):
    pass


class RuntimeSandboxUnavailableError(RuntimePathInvalidError):
    pass


class RuntimeWorkspaceFiles:
    def __init__(self, ssh: RuntimeTransport, *, workspace_location: WorkspaceLocation) -> None:
        self._ssh = ssh
        self._workspace_location = workspace_location
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

    async def download(
        self, session_id: UUID | str, *, path: str, range_header=None, if_range=None, head=False
    ):
        await self._require_supported_environment()
        if not isinstance(self._ssh, container_transport.ContainerTransport):
            raise RuntimeSandboxUnavailableError("An attached workspace container is required")
        workspace = SimpleNamespace(
            id=self._ssh.workspace_id,
            directory=self._ssh.directory,
            development_tools=self._ssh.tools,
        )
        return await file_stream.open_file(
            workspace, path, download=True, range_header=range_header, if_range=if_range, head=head
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
        paths = workspace_paths(str(session_id), root=self._workspace_location)
        await self._require_supported_environment()
        request = {
            "operation": operation,
            "session_id": paths.session_id,
            "session_root": paths.session_root,
            "workspace": paths.workspace,
            "payload": payload,
        }
        result = await self._ssh.run(
            guest_python_command(
                "common/files/operations.py", [json.dumps(request, separators=(",", ":"))]
            ),
            timeout=timeout,
        )
        if result.exit_status not in {0, None}:
            detail = (result.stderr or result.stdout or "").strip()[:500]
            _raise_remote_error("runtime_error", detail)
        try:
            response = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimePathInvalidError("Machine file response was not valid JSON") from exc
        if not isinstance(response, dict):
            raise RuntimePathInvalidError("Machine file response was not an object")
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
                "An attached workspace container is required "
                f"(detected os={environment.os}, sandbox={environment.sandbox})."
            )
        return environment


def _raise_remote_error(error: str, detail: str) -> None:
    if error == "not_found":
        raise RuntimePathNotFoundError(detail or "Machine path not found")
    if error == "is_directory":
        raise RuntimePathIsDirectoryError(detail or "Machine path is a directory")
    raise RuntimePathInvalidError(detail or "Invalid runtime path")
