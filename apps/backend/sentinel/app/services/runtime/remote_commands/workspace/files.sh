#!/usr/bin/env bash
set -euo pipefail
python3 - "$1" <<'PY'
from __future__ import annotations

import base64
import difflib
import io
import json
import mimetypes
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUEST = json.loads(sys.argv[1])
SESSION_ID = str(REQUEST["session_id"])
SESSION_ROOT = Path(REQUEST["session_root"])
WORKSPACE = Path(REQUEST["workspace"])
PAYLOAD = REQUEST.get("payload") if isinstance(REQUEST.get("payload"), dict) else {}
OPERATION = str(REQUEST["operation"])


class RuntimePathError(Exception):
    code = "invalid_path"


class RuntimeNotFound(RuntimePathError):
    code = "not_found"


class RuntimeIsDirectory(RuntimePathError):
    code = "is_directory"


def main() -> None:
    try:
        operations = {
            "list_files": list_files,
            "preview_file": preview_file,
            "download": download,
            "git_roots": git_roots,
            "git_changed": git_changed,
            "git_diff": git_diff,
            "str_replace": str_replace,
        }
        handler = operations.get(OPERATION)
        if handler is None:
            raise RuntimePathError(f"unsupported operation: {OPERATION}")
        emit({"ok": True, "data": handler()})
    except RuntimePathError as exc:
        emit({"ok": False, "error": exc.code, "detail": str(exc)})
        sys.exit(0)
    except Exception as exc:
        emit({"ok": False, "error": "runtime_error", "detail": str(exc)})
        sys.exit(0)


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


def normalize_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw == ".":
        return ""
    parts = [part for part in raw.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise RuntimePathError("Path traversal is not allowed")
    return "/".join(parts)


def workspace_exists_payload(path: str) -> dict[str, Any]:
    return {
        "session_id": SESSION_ID,
        "runtime_exists": SESSION_ROOT.exists(),
        "workspace_exists": WORKSPACE.exists(),
        "path": path,
    }


def resolve_workspace_path(path: str, *, must_exist: bool = True) -> Path:
    base = WORKSPACE.resolve()
    target = (WORKSPACE / path).resolve(strict=False) if path else base
    if target != base and base not in target.parents:
        raise RuntimePathError("Path must stay within runtime workspace")
    if must_exist and not target.exists():
        raise RuntimeNotFound(path or ".")
    return target


def parent_path(path: str) -> str | None:
    if not path:
        return None
    parent = Path(path).parent.as_posix()
    return "" if parent in {"", "."} else parent


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


def run(command: list[str], *, cwd: Path | None = None, timeout: float = 8) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def is_within(base: Path, target: Path) -> bool:
    base = base.resolve()
    target = target.resolve(strict=False)
    return target == base or base in target.parents


def resolve_git_root(start: Path) -> Path | None:
    code, stdout, _stderr = run(["git", "-C", str(start), "rev-parse", "--show-toplevel"], timeout=4)
    if code != 0:
        return None
    value = stdout.strip()
    if not value:
        return None
    root = Path(value).resolve(strict=False)
    return root if is_within(WORKSPACE, root) else None


def git_root_payload(root: Path) -> dict[str, Any]:
    try:
        rel = root.relative_to(WORKSPACE.resolve()).as_posix()
    except ValueError:
        rel = ""
    code, stdout, _stderr = run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"], timeout=4)
    branch_raw = stdout.strip() if code == 0 else ""
    detached = branch_raw == "HEAD" or not branch_raw
    return {"root_path": rel, "branch": None if detached else branch_raw, "detached_head": detached}


def entry_for(child: Path) -> dict[str, Any]:
    stats = child.stat()
    try:
        rel = child.relative_to(WORKSPACE.resolve()).as_posix()
    except ValueError:
        rel = child.name
    git_payload = None
    if child.is_dir() and (child / ".git").exists():
        root = resolve_git_root(child)
        if root is not None and root == child.resolve(strict=False):
            git_payload = git_root_payload(root)
    return {
        "name": child.name,
        "path": "" if rel == "." else rel,
        "kind": "directory" if child.is_dir() else "file",
        "size_bytes": None if child.is_dir() else int(stats.st_size),
        "modified_at": iso_mtime(child),
        "is_git_root": git_payload is not None,
        "git_branch": git_payload.get("branch") if git_payload else None,
        "git_detached_head": bool(git_payload.get("detached_head")) if git_payload else False,
    }


def list_files() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    response = workspace_exists_payload(path)
    response.update({"parent_path": parent_path(path), "entries": [], "truncated": False})
    if not WORKSPACE.exists():
        return response
    target = resolve_workspace_path(path)
    if not target.is_dir():
        raise RuntimePathError("Runtime path is not a directory")
    limit = normalize_limit(PAYLOAD.get("limit"), 400, 1, 2000)
    children = sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
    entries = []
    for index, child in enumerate(children):
        if index >= limit:
            response["truncated"] = True
            break
        entries.append(entry_for(child))
    response["entries"] = entries
    return response


def preview_file() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    if not path:
        raise RuntimeIsDirectory("workspace root")
    target = resolve_workspace_path(path)
    if not target.is_file():
        raise RuntimeIsDirectory(path)
    max_bytes = normalize_limit(PAYLOAD.get("max_bytes"), 32000, 256, 200000)
    raw = target.read_bytes()
    snippet = raw[:max_bytes]
    response = workspace_exists_payload(path)
    response.update({
        "name": target.name,
        "size_bytes": int(target.stat().st_size),
        "modified_at": iso_mtime(target),
        "content": snippet.decode("utf-8", errors="replace"),
        "truncated": len(raw) > max_bytes,
        "max_bytes": max_bytes,
    })
    return response


def download() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    if not path:
        raise RuntimePathError("Runtime path is required")
    target = resolve_workspace_path(path)
    if target.is_file():
        content = target.read_bytes()
        return {
            "download_name": target.name,
            "media_type": mimetypes.guess_type(target.name)[0] or "application/octet-stream",
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
    if not target.is_dir():
        raise RuntimePathError("Runtime path must be a file or directory")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for current in sorted(target.rglob("*")):
            rel = current.relative_to(target).as_posix()
            if current.is_dir():
                if not any(current.iterdir()):
                    archive.writestr(f"{rel.rstrip('/')}/", "")
                continue
            archive.write(current, arcname=rel)
    return {
        "download_name": f"{target.name or 'workspace'}.zip",
        "media_type": "application/zip",
        "content_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
    }


def scan_git_roots(limit: int) -> list[Path]:
    skip = {".venv", "node_modules", "dist", "build", "__pycache__", ".mypy_cache", ".pytest_cache"}
    roots = []
    for current, dirs, _files in os.walk(WORKSPACE):
        if len(roots) >= limit:
            break
        dirs[:] = [name for name in dirs if name not in skip]
        if ".git" in dirs:
            root = Path(current).resolve(strict=False)
            if is_within(WORKSPACE, root):
                roots.append(root)
            dirs.remove(".git")
    return roots


def git_roots() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    limit = normalize_limit(PAYLOAD.get("limit"), 200, 1, 1000)
    response = workspace_exists_payload(path)
    response["roots"] = []
    if not WORKSPACE.exists():
        return response
    if path:
        target = resolve_workspace_path(path)
        probe = target if target.is_dir() else target.parent
        root = resolve_git_root(probe)
        if root is not None:
            response["roots"].append(git_root_payload(root))
    else:
        seen = set()
        for root in scan_git_roots(limit):
            item = git_root_payload(root)
            if item["root_path"] in seen:
                continue
            seen.add(item["root_path"])
            response["roots"].append(item)
            if len(response["roots"]) >= limit:
                break
    return response


def git_changed() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    limit = normalize_limit(PAYLOAD.get("limit"), 200, 1, 1000)
    response = workspace_exists_payload(path)
    response.update({"git_root": "", "branch": None, "detached_head": False, "entries": [], "truncated": False})
    if not WORKSPACE.exists():
        return response
    target = resolve_workspace_path(path) if path else WORKSPACE.resolve()
    probe = target if target.is_dir() else target.parent
    root = resolve_git_root(probe)
    if root is None:
        raise RuntimePathError("Path is not inside a git repository within runtime workspace")
    root_info = git_root_payload(root)
    code, stdout, stderr = run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all", "--", "."], timeout=8)
    if code != 0:
        raise RuntimePathError(stderr.strip() or stdout.strip() or "git status failed")
    prefix = root_info["root_path"].strip("/")
    for index, line in enumerate(stdout.splitlines()):
        if not line.strip():
            continue
        if index >= limit:
            response["truncated"] = True
            break
        status = line[:2] if len(line) >= 2 else line
        file_path = line[3:] if len(line) >= 3 else ""
        if " -> " in file_path:
            file_path = file_path.split(" -> ", 1)[1]
        file_path = file_path.strip()
        if not file_path:
            continue
        untracked = status == "??"
        response["entries"].append({
            "path": f"{prefix}/{file_path}" if prefix else file_path,
            "status": status.strip() or status,
            "staged": False if untracked else status[0] != " ",
            "unstaged": False if untracked else len(status) > 1 and status[1] != " ",
            "untracked": untracked,
        })
    response.update({"git_root": root_info["root_path"], "branch": root_info["branch"], "detached_head": root_info["detached_head"]})
    return response


def truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text, False
    return raw[:max_bytes].decode("utf-8", errors="replace"), True


def git_diff() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    if not path:
        raise RuntimeIsDirectory("workspace root")
    target = resolve_workspace_path(path, must_exist=False)
    probe = target if target.exists() and target.is_dir() else target.parent
    while probe != WORKSPACE.resolve() and not probe.exists():
        probe = probe.parent
    if not probe.exists():
        raise RuntimeNotFound(path)
    if target.exists() and not target.is_file():
        raise RuntimeIsDirectory(path)
    root = resolve_git_root(probe if probe.is_dir() else probe.parent)
    if root is None:
        raise RuntimePathError("Path is not inside a git repository within runtime workspace")
    root_info = git_root_payload(root)
    file_rel = target.relative_to(root).as_posix()
    base_ref = str(PAYLOAD.get("base_ref") or "HEAD").strip() or "HEAD"
    context_lines = normalize_limit(PAYLOAD.get("context_lines"), 3, 0, 20)
    max_bytes = normalize_limit(PAYLOAD.get("max_bytes"), 120000, 1024, 500000)
    command = ["git", "-C", str(root), "diff", f"--unified={context_lines}"]
    if bool(PAYLOAD.get("staged")):
        command.append("--staged")
    if base_ref:
        command.append(base_ref)
    command.extend(["--", file_rel])
    code, stdout, stderr = run(command, timeout=8)
    if code != 0:
        raise RuntimePathError(stderr.strip() or stdout.strip() or "git diff failed")
    if not stdout and target.exists():
        tracked_code, _tracked_stdout, _tracked_stderr = run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", file_rel], timeout=4)
        if tracked_code != 0:
            text = target.read_text(encoding="utf-8", errors="replace")
            stdout = "".join(difflib.unified_diff([], text.splitlines(keepends=True), fromfile="/dev/null", tofile=f"b/{file_rel}", n=context_lines))
    diff, truncated = truncate_utf8(stdout, max_bytes)
    response = workspace_exists_payload(path)
    response.update({
        "git_root": root_info["root_path"],
        "branch": root_info["branch"],
        "detached_head": root_info["detached_head"],
        "base_ref": base_ref,
        "staged": bool(PAYLOAD.get("staged")),
        "context_lines": context_lines,
        "diff": diff,
        "truncated": truncated,
        "max_bytes": max_bytes,
    })
    return response


def str_replace() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    old_str = PAYLOAD.get("old_str")
    new_str = PAYLOAD.get("new_str")
    if not path:
        raise RuntimePathError("Field 'path' must be a non-empty string")
    if not isinstance(old_str, str):
        raise RuntimePathError("Field 'old_str' must be a string")
    if old_str == "":
        raise RuntimePathError("Field 'old_str' must be a non-empty string")
    if not isinstance(new_str, str):
        raise RuntimePathError("Field 'new_str' must be a string")
    target = resolve_workspace_path(path)
    if not target.is_file():
        raise RuntimeIsDirectory(path)
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise RuntimePathError(f"File is not UTF-8 text: {path}")
    first = content.find(old_str)
    if first < 0:
        raise RuntimePathError(
            f"The exact string to replace was not found in {path}. "
            "Check for whitespace/indentation issues."
        )
    second = content.find(old_str, first + 1)
    if second >= 0:
        raise RuntimePathError(
            f"The string to replace occurs multiple times in {path}. "
            "Please provide a more unique block of context."
        )
    updated = content[:first] + new_str + content[first + len(old_str):]
    target.write_text(updated, encoding="utf-8")
    return {
        "path": path,
        "message": "File patched successfully",
        "old_str_count": 1,
        "size_bytes": int(target.stat().st_size),
        "modified_at": iso_mtime(target),
    }


main()
PY
