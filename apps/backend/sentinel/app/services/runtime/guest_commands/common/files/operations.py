from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from sentinel_guest_files import (
    media_type_for,
    RuntimePathError,
    RuntimeIsDirectory,
    emit,
    normalize_path,
    response_path,
    workspace_exists_payload,
    resolve_container_path,
    parent_path,
    iso_mtime,
    normalize_limit,
    WORKSPACE,
    PAYLOAD,
    OPERATION,
)
from sentinel_guest_git import (
    resolve_git_root,
    git_root_payload,
    git_roots,
    git_context,
    git_history,
    git_changed,
    git_diff,
)


def entry_for(child: Path) -> dict[str, Any]:
    stats = child.stat()
    rel = response_path(child)
    git_payload = None
    if child.is_dir() and (child / ".git").exists():
        try:
            root = resolve_git_root(child)
            if root is not None and root == child.resolve(strict=False):
                git_payload = git_root_payload(root)
        except RuntimePathError:
            pass
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
    if not path and not WORKSPACE.exists():
        return response
    target = resolve_container_path(path)
    if not target.is_dir():
        raise RuntimePathError("Runtime path is not a directory")
    limit = normalize_limit(PAYLOAD.get("limit"), 400, 1, 2000)
    children = sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
    entries = []
    for index, child in enumerate(children):
        if index >= limit:
            response["truncated"] = True
            break
        try:
            entries.append(entry_for(child))
        except OSError:
            # An unreadable or broken symlink must not hide the whole directory.
            continue
    response["entries"] = entries
    return response


def preview_file() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    if not path:
        raise RuntimeIsDirectory("workspace root")
    target = resolve_container_path(path)
    if not target.is_file():
        raise RuntimeIsDirectory(path)
    max_bytes = normalize_limit(PAYLOAD.get("max_bytes"), 32000, 256, 200000)
    media_type = media_type_for(target)
    media = media_type == "application/pdf" or media_type.startswith(("image/", "audio/", "video/"))
    with target.open("rb") as stream:
        raw = b"" if media else stream.read(max_bytes + 1)
    snippet = raw[:max_bytes]
    response = workspace_exists_payload(path)
    response.update(
        {
            "name": target.name,
            "size_bytes": int(target.stat().st_size),
            "modified_at": iso_mtime(target),
            "content": "" if b"\0" in snippet else snippet.decode("utf-8", errors="replace"),
            "binary": media or b"\0" in snippet,
            "media_type": media_type,
            "truncated": len(raw) > max_bytes,
            "max_bytes": max_bytes,
        }
    )
    return response


def search_files() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    target = resolve_container_path(path)
    query = str(PAYLOAD.get("query", "")).casefold()
    limit = normalize_limit(PAYLOAD.get("limit"), 100, 1, 500)
    skip = {".git", "node_modules", ".venv", "__pycache__", "dist", "build"}
    results = []
    deadline = time.monotonic() + 3
    visited = 0
    for current, dirs, files in os.walk(target):
        dirs[:] = sorted(name for name in dirs if name not in skip)
        visited += 1
        if time.monotonic() > deadline or visited > 10000:
            return {"entries": results, "truncated": True}
        for name in sorted(files):
            child = Path(current) / name
            if query not in child.relative_to(target).as_posix().casefold():
                continue
            try:
                results.append(entry_for(child))
            except OSError:
                continue
            if len(results) >= limit:
                return {"entries": results, "truncated": True}
    return {"entries": results, "truncated": False}


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
    target = resolve_container_path(path)
    if not target.is_file():
        raise RuntimeIsDirectory(path)
    try:
        # Exact replacement must not normalize line endings outside the match.
        content = target.read_bytes().decode("utf-8")
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
    updated = content[:first] + new_str + content[first + len(old_str) :]
    target.write_bytes(updated.encode("utf-8"))
    return {
        "path": path,
        "message": "File patched successfully",
        "old_str_count": 1,
        "size_bytes": int(target.stat().st_size),
        "modified_at": iso_mtime(target),
    }


def main() -> None:
    try:
        operations = {
            "list_files": list_files,
            "preview_file": preview_file,
            "git_roots": git_roots,
            "git_changed": git_changed,
            "git_diff": git_diff,
            "git_context": git_context,
            "git_history": git_history,
            "search_files": search_files,
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


main()
