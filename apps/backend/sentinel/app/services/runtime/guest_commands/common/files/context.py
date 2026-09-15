from __future__ import annotations

import json
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUEST = json.loads(sys.argv[1])
SESSION_ID = str(REQUEST.get("session_id", ""))
SESSION_ROOT = Path(REQUEST.get("session_root", "/var/lib/sentinel"))
WORKSPACE = Path(REQUEST["workspace"])
PAYLOAD = REQUEST.get("payload") if isinstance(REQUEST.get("payload"), dict) else {}
OPERATION = str(REQUEST.get("operation", ""))


class RuntimePathError(Exception):
    code = "invalid_path"


class RuntimeNotFound(RuntimePathError):
    code = "not_found"


class RuntimeIsDirectory(RuntimePathError):
    code = "is_directory"


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


def normalize_path(value: Any) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or "\0" in value:
        raise RuntimePathError("Path must be a string without null bytes")
    return response_path(resolve_container_path(value, must_exist=False))


def response_path(path: Path) -> str:
    # Keep project-relative response paths for the explorer; preserve absolute
    # paths everywhere else in the container so entries can be used verbatim.
    try:
        relative = path.relative_to(WORKSPACE.resolve()).as_posix()
        return "" if relative == "." else relative
    except ValueError:
        return path.as_posix()


def workspace_exists_payload(path: str) -> dict[str, Any]:
    return {
        "session_id": SESSION_ID,
        "runtime_exists": SESSION_ROOT.exists(),
        "workspace_exists": WORKSPACE.exists(),
        "path": path,
    }


def resolve_container_path(path: str, *, must_exist: bool = True) -> Path:
    candidate = Path(path).expanduser() if path else WORKSPACE
    if not candidate.is_absolute():
        candidate = WORKSPACE / candidate
    # This helper runs inside ContainerTransport. The container, rather than a
    # project-directory prefix, is the filesystem boundary (including symlinks).
    target = candidate.resolve(strict=False)
    if must_exist and not target.exists():
        raise RuntimeNotFound(path or ".")
    return target


def parent_path(path: str) -> str | None:
    if not path or path == "/":
        return None
    parent = Path(path).parent.as_posix()
    return "" if parent == "." else parent


def iso_mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def normalize_limit(value: Any, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def is_within(base: Path, target: Path) -> bool:
    base = base.resolve()
    target = target.resolve(strict=False)
    return target == base or base in target.parents


def truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text, False
    return raw[:max_bytes].decode("utf-8", errors="replace"), True


def media_type_for(path: Path) -> str:
    # In source workspaces these extensions identify TypeScript, not MPEG streams.
    if path.suffix.lower() in {".ts", ".tsx", ".mts", ".cts"}:
        return "text/typescript"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
