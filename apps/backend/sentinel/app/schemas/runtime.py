from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class RuntimeExecResult:
    exit_status: int | None
    stdout: str
    stderr: str


class RuntimeProviderInfoItemResponse(BaseModel):
    key: str
    label: str
    value: str


class RuntimeProviderInfoResponse(BaseModel):
    id: str
    label: str
    status: str | None = None
    summary: str | None = None
    items: list[RuntimeProviderInfoItemResponse] = []


class RuntimeLiveViewResponse(BaseModel):
    enabled: bool
    available: bool
    mode: str = "none"
    url: str | None = None
    ws_url: str | None = None
    display: str | None = None
    geometry: str | None = None
    reason: str | None = None
    provider: RuntimeProviderInfoResponse


class RuntimeDesktopResolutionRequest(BaseModel):
    geometry: str


class RuntimeResetResponse(BaseModel):
    reset: bool
    url: str
    profile_dir: str | None = None
    stale_lock_cleared: bool = False


class RuntimeActionResponse(BaseModel):
    ok: bool
    action: str
    session_id: UUID
    detail: str | None = None
    result: dict[str, object] = Field(default_factory=dict)


class RuntimeStatusTargetResponse(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    workspaces_dir: str | None = None


class RuntimeStatusCheckResponse(BaseModel):
    id: str
    label: str
    status: Literal["pass", "fail", "warn", "skip"]
    detail: str | None = None
    hint: str | None = None
    required: bool = True
    duration_ms: int | None = None


class RuntimeStatusResponse(BaseModel):
    status: Literal["ready", "degraded", "not_configured", "unreachable", "failed"]
    summary: str
    checked_at: datetime
    os: Literal["linux", "darwin", "unsupported", "unknown"] = "unknown"
    sandbox: Literal["bubblewrap", "seatbelt", "unavailable", "unknown"] = "unknown"
    target: RuntimeStatusTargetResponse
    checks: list[RuntimeStatusCheckResponse] = Field(default_factory=list)
    capabilities: dict[str, str] = Field(default_factory=dict)


class SessionRuntimeFileEntryResponse(BaseModel):
    name: str
    path: str
    kind: Literal["file", "directory"]
    size_bytes: int | None = None
    modified_at: datetime | None = None
    is_git_root: bool = False
    git_branch: str | None = None
    git_detached_head: bool = False


class SessionRuntimeFilesResponse(BaseModel):
    session_id: UUID
    runtime_exists: bool
    workspace_exists: bool
    path: str
    parent_path: str | None = None
    entries: list[SessionRuntimeFileEntryResponse] = Field(default_factory=list)
    truncated: bool = False


class SessionRuntimeFilePreviewResponse(BaseModel):
    session_id: UUID
    runtime_exists: bool
    workspace_exists: bool
    path: str
    name: str
    size_bytes: int
    modified_at: datetime | None = None
    content: str
    truncated: bool = False
    max_bytes: int


class SessionRuntimeGitRootResponse(BaseModel):
    root_path: str
    branch: str | None = None
    detached_head: bool = False


class SessionRuntimeGitRootsResponse(BaseModel):
    session_id: UUID
    runtime_exists: bool
    workspace_exists: bool
    path: str
    roots: list[SessionRuntimeGitRootResponse] = Field(default_factory=list)


class SessionRuntimeGitDiffResponse(BaseModel):
    session_id: UUID
    runtime_exists: bool
    workspace_exists: bool
    path: str
    git_root: str
    branch: str | None = None
    detached_head: bool = False
    base_ref: str
    staged: bool = False
    context_lines: int = 3
    diff: str
    truncated: bool = False
    max_bytes: int


class SessionRuntimeGitChangedFileResponse(BaseModel):
    path: str
    status: str
    staged: bool = False
    unstaged: bool = False
    untracked: bool = False


class SessionRuntimeGitChangedFilesResponse(BaseModel):
    session_id: UUID
    runtime_exists: bool
    workspace_exists: bool
    path: str
    git_root: str
    branch: str | None = None
    detached_head: bool = False
    entries: list[SessionRuntimeGitChangedFileResponse] = Field(default_factory=list)
    truncated: bool = False
