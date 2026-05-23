from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from app.services.runtime.remote_commands import load_remote_command


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class RuntimeWorkspaceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RemoteWorkspacePaths:
    session_id: str
    workspaces_root: str
    session_root: str
    workspace: str
    state: str
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
            "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "paths": {
                "workspaces_root": self.workspaces_root,
                "session_root": self.session_root,
                "workspace": self.workspace,
                "state": self.state,
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
        raise RuntimeWorkspaceError(
            "session id must match [A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
        )
    return session_id


def normalize_workspaces_root(root: str | None = None) -> str:
    value = (root or "").strip()
    if not value:
        raise RuntimeWorkspaceError("runtime workspaces dir cannot be empty")
    if "\x00" in value or "\n" in value:
        raise RuntimeWorkspaceError("runtime workspaces dir cannot contain NUL or newline")
    path = PurePosixPath(value)
    if not path.is_absolute():
        raise RuntimeWorkspaceError("runtime workspaces dir must be an absolute POSIX path")
    return path.as_posix().rstrip("/") or "/"


def workspace_paths(session_id: str, *, root: str | None = None) -> RemoteWorkspacePaths:
    session_id = validate_session_id(session_id)
    workspaces_root = normalize_workspaces_root(root)
    session_root = (PurePosixPath(workspaces_root) / session_id).as_posix()
    state = (PurePosixPath(session_root) / "state").as_posix()
    return RemoteWorkspacePaths(
        session_id=session_id,
        workspaces_root=workspaces_root,
        session_root=session_root,
        workspace=(PurePosixPath(session_root) / "workspace").as_posix(),
        state=state,
        home=(PurePosixPath(state) / "home").as_posix(),
        runtime=(PurePosixPath(state) / "runtime").as_posix(),
        tmux=(PurePosixPath(state) / "tmux").as_posix(),
        browser=(PurePosixPath(state) / "browser").as_posix(),
        tmp=(PurePosixPath(session_root) / "tmp").as_posix(),
        logs=(PurePosixPath(session_root) / "logs").as_posix(),
        manifest=(PurePosixPath(session_root) / "manifest.json").as_posix(),
    )


def build_prepare_workspace_script(session_id: str, *, root: str | None = None) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    directories = [
        paths.workspaces_root,
        paths.session_root,
        paths.workspace,
        paths.state,
        paths.home,
        paths.runtime,
        paths.tmux,
        paths.browser,
        paths.tmp,
        paths.logs,
    ]
    request = {
        "session_root": paths.session_root,
        "workspaces_root": paths.workspaces_root,
        "manifest_path": paths.manifest,
        "directories": directories,
        "private_directories": [
            paths.session_root,
            paths.workspace,
            paths.state,
            paths.home,
            paths.runtime,
            paths.tmux,
            paths.browser,
            paths.tmp,
            paths.logs,
        ],
        "manifest": paths.manifest_payload(),
    }
    return load_remote_command("common/workspace/prepare.sh"), [json.dumps(request, separators=(",", ":"))]


def build_delete_workspace_script(session_id: str, *, root: str | None = None) -> tuple[str, list[str]]:
    paths = workspace_paths(session_id, root=root)
    request = {
        "session_root": paths.session_root,
        "workspaces_root": paths.workspaces_root,
    }
    return load_remote_command("common/workspace/delete.sh"), [json.dumps(request, separators=(",", ":"))]
