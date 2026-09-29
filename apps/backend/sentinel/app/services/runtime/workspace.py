from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from app.services.runtime.guest_commands import load_guest_command

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class RuntimeWorkspaceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkspaceLocation:
    """User project and Sentinel-owned storage for one persistent workspace."""

    directory: str
    state_root: str
    workspace_id: str = ""
    host_directory: str = ""
    tools: tuple[str, ...] = ()
    distribution: str = "alpine"
    desktop: str = "none"


@dataclass(frozen=True, slots=True)
class RemoteWorkspacePaths:
    session_id: str
    control_root: str
    session_root: str
    workspace: str
    cache: str
    home: str
    runtime: str
    tmux: str
    browser: str
    tmp: str
    logs: str
    manifest: str

    def manifest_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "session_id": self.session_id,
            "created_at": datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "paths": {
                "control_root": self.control_root,
                "session_root": self.session_root,
                "workspace": self.workspace,
                "cache": self.cache,
                "home": self.home,
                "runtime": self.runtime,
                "tmux": self.tmux,
                "browser": self.browser,
                "tmp": self.tmp,
                "logs": self.logs,
            },
        }


def validate_session_id(session_id: str) -> str:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise RuntimeWorkspaceError("session id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
    return session_id


def validate_absolute_directory(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or any(c in value for c in ("\x00", "\n", "\r"))
        or not path.is_absolute()
        or ".." in path.parts
        or str(path) == "/"
    ):
        raise RuntimeWorkspaceError("Expected an absolute non-root directory without traversal")
    return str(path)


def workspace_paths(session_id: str, *, root: WorkspaceLocation) -> RemoteWorkspacePaths:
    session_id = validate_session_id(session_id)
    if not isinstance(root, WorkspaceLocation):
        raise RuntimeWorkspaceError("An explicit workspace location is required")
    project = validate_absolute_directory(root.directory)
    managed = PurePosixPath(validate_absolute_directory(root.state_root))
    control_root = managed / "control"
    session_root = control_root / session_id
    return RemoteWorkspacePaths(
        session_id=session_id,
        control_root=str(control_root),
        session_root=str(session_root),
        workspace=project,
        home="/root",
        cache="/root/.cache",
        tmp="/tmp",
        runtime=str(session_root / "runtime"),
        tmux=str(session_root / "tmux"),
        browser=str(session_root / "browser"),
        logs=str(session_root / "logs"),
        manifest=str(session_root / "manifest.json"),
    )


def build_prepare_workspace_script(
    session_id: str, *, root: WorkspaceLocation
) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    directories = [
        paths.control_root,
        paths.session_root,
        paths.workspace,
        paths.cache,
        paths.home,
        paths.runtime,
        paths.tmux,
        paths.browser,
        paths.tmp,
        paths.logs,
    ]
    return load_guest_command("common/workspace/prepare.sh"), [
        paths.workspace,
        paths.control_root,
        paths.session_root,
        json.dumps(paths.manifest_payload(), separators=(",", ":")),
        *[path for path in directories if path not in {paths.workspace, paths.control_root}],
    ]


def build_delete_session_script(
    session_id: str, *, root: WorkspaceLocation
) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    return load_guest_command("common/workspace/delete.sh"), [
        paths.control_root,
        paths.session_root,
    ]
