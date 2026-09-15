from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from sentinel_guest_files import (
    RuntimePathError,
    RuntimeNotFound,
    RuntimeIsDirectory,
    normalize_path,
    response_path,
    workspace_exists_payload,
    resolve_container_path,
    normalize_limit,
    truncate_utf8,
    WORKSPACE,
    PAYLOAD,
)


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


def resolve_git_root(start: Path) -> Path | None:
    code, stdout, stderr = run(["git", "-C", str(start), "rev-parse", "--show-toplevel"], timeout=4)
    if code != 0:
        if "not a git repository" in stderr.lower():
            # A linked checkout can exist while its metadata is not mounted.
            for parent in (start, *start.parents):
                marker = parent / ".git"
                if marker.is_file():
                    raise RuntimePathError(
                        "This worktree's Git metadata is unavailable inside the workspace. "
                        "Its .git file may point outside the mounted project."
                    )
                if parent == WORKSPACE or parent == parent.parent:
                    break
            return None
        raise RuntimePathError(stderr.strip() or "Git could not inspect this folder")
    return Path(stdout.strip()).resolve(strict=False) if stdout.strip() else None


def git_output(root: Path, *args: str, timeout: int = 8) -> str:
    code, stdout, stderr = run(
        ["git", "--no-optional-locks", "-C", str(root), *args], timeout=timeout
    )
    if code:
        if args and args[0] == "status" and "timed out" in stderr:
            raise RuntimePathError("Git status is taking longer than expected. Retry in a moment.")
        raise RuntimePathError(stderr.strip() or "Git command failed")
    return stdout


def git_root_payload(root: Path, *, include_refs: bool = True) -> dict[str, Any]:
    code, branch, _ = run(["git", "-C", str(root), "symbolic-ref", "--short", "HEAD"], timeout=4)
    refs = (
        git_output(root, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes")
        if include_refs
        else ""
    )
    return {
        "root_path": response_path(root),
        "branch": branch.strip() if code == 0 else None,
        "detached_head": code != 0,
        "refs": refs.splitlines(),
    }


def scan_git_roots(limit: int) -> list[Path]:
    skip = {".git"}
    if not PAYLOAD.get("include_generated", False):
        skip.update(
            {
                ".venv",
                "node_modules",
                "dist",
                "build",
                "__pycache__",
                ".mypy_cache",
                ".pytest_cache",
                ".terraform",
                ".cache",
                ".next",
                ".nuxt",
                ".tox",
                ".yarn",
                ".pnpm-store",
                "coverage",
            }
        )
    roots = []
    deadline = time.monotonic() + 3
    for current, dirs, files in os.walk(WORKSPACE):
        if len(roots) >= limit or time.monotonic() > deadline:
            break
        if ".git" in dirs or ".git" in files:
            roots.append(Path(current).resolve(strict=False))
        dirs[:] = [name for name in dirs if name not in skip]
    return roots


def git_roots() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    limit = normalize_limit(PAYLOAD.get("limit"), 200, 1, 1000)
    response = workspace_exists_payload(path)
    response.update({"roots": [], "errors": []})
    if not WORKSPACE.exists():
        return response
    target = resolve_container_path(path) if path else WORKSPACE
    candidates = (
        [target if target.is_dir() else target.parent]
        if path
        else list(dict.fromkeys([WORKSPACE, *scan_git_roots(limit)]))
    )
    seen = set()
    repositories = {}
    for candidate in candidates:
        try:
            root = resolve_git_root(candidate)
            if root is None or str(root) in seen:
                continue
            seen.add(str(root))
            payload = git_root_payload(root)
            common = git_output(root, "rev-parse", "--git-common-dir").strip()
            payload["common_dir"] = str((root / common).resolve())
            if PAYLOAD.get("group_worktrees", False):
                key = payload["common_dir"]
                if key not in repositories or (root / ".git").is_dir():
                    repositories[key] = payload
            else:
                response["roots"].append(payload)
        except RuntimePathError as exc:
            response["errors"].append({"path": response_path(candidate), "message": str(exc)})
    if PAYLOAD.get("group_worktrees", False):
        response["roots"] = sorted(
            repositories.values(), key=lambda item: item["root_path"].casefold()
        )
    return response


def git_context() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    target = resolve_container_path(path)
    root = resolve_git_root(target if target.is_dir() else target.parent)
    if root is None:
        return {"repository": None, "worktrees": []}
    repository = git_root_payload(root)
    common = git_output(root, "rev-parse", "--git-common-dir").strip()
    repository["common_dir"] = str((root / common).resolve())
    code, upstream, _ = run(
        ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "@{upstream}"], timeout=4
    )
    repository["upstream"] = upstream.strip() if code == 0 else None
    if not PAYLOAD.get("include_worktrees", True):
        return {"repository": repository, "worktrees": []}
    records = git_output(root, "worktree", "list", "--porcelain", "-z")
    worktrees, record = [], {}
    for field in records.split("\0"):
        if not field:
            if record:
                location = Path(record["worktree"])
                available = False
                try:
                    available = (
                        location.is_dir() and resolve_git_root(location) == location.resolve()
                    )
                except RuntimePathError:
                    pass
                worktrees.append(
                    {
                        "path": response_path(location),
                        "branch": record.get("branch", "").removeprefix("refs/heads/") or None,
                        "head": record.get("HEAD"),
                        "available": available,
                        "current": location.resolve(strict=False) == root,
                        "locked": "locked" in record,
                        "prunable": "prunable" in record,
                    }
                )
                record = {}
        else:
            key, _, value = field.partition(" ")
            record[key] = value
    # One batch for all heads, including detached worktrees. Missing/prunable
    # commits must not prevent browsing the remaining checkouts.
    heads = sorted(
        {tree["head"] for tree in worktrees if tree.get("head") and set(tree["head"]) != {"0"}}
    )
    dates = {}
    if heads:
        code, output, _ = run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "--no-walk",
                "--ignore-missing",
                "--format=%H %ct",
                *heads,
                "--",
            ],
            timeout=8,
        )
        if code == 0:
            for line in output.splitlines():
                commit, _, timestamp = line.partition(" ")
                if timestamp.isdigit():
                    dates[commit] = int(timestamp)
    for tree in worktrees:
        tree["last_commit_at"] = dates.get(tree["head"])
    return {"repository": repository, "worktrees": worktrees}


def git_history() -> dict[str, Any]:
    target = resolve_container_path(normalize_path(PAYLOAD.get("path")))
    root = resolve_git_root(target)
    if root is None:
        raise RuntimePathError("This folder is not a Git repository")
    code, _, _ = run(["git", "-C", str(root), "rev-parse", "--verify", "HEAD"], timeout=4)
    if code:
        return {"commits": []}
    limit = normalize_limit(PAYLOAD.get("limit"), 50, 1, 200)
    output = git_output(
        root, "log", f"-{limit}", "--format=%H%x00%h%x00%s%x00%an%x00%aI%x00", "--no-show-signature"
    )
    fields = output.split("\0")
    commits = []
    for i in range(0, len(fields) - 5, 5):
        commits.append(
            dict(
                zip(
                    ("id", "short_id", "subject", "author", "date"),
                    [fields[i].lstrip("\n"), *fields[i + 1 : i + 5]],
                )
            )
        )
    return {"commits": commits}


def git_changed() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    limit = normalize_limit(PAYLOAD.get("limit"), 200, 1, 2000)
    response = workspace_exists_payload(path)
    response.update(
        {"git_root": "", "branch": None, "detached_head": False, "entries": [], "truncated": False}
    )
    if not path and not WORKSPACE.exists():
        return response
    target = resolve_container_path(path) if path else WORKSPACE.resolve()
    probe = target if target.is_dir() else target.parent
    root = resolve_git_root(probe)
    if root is None:
        raise RuntimePathError("Path is not inside a git repository")
    root_info = git_root_payload(root, include_refs=False)
    stdout = git_output(
        root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".", timeout=20
    )
    records = iter(stdout.split("\0"))
    for record in records:
        if not record:
            continue
        status, file_path = record[:2], record[3:]
        original_path = next(records, None) if "R" in status or "C" in status else None
        if len(response["entries"]) >= limit:
            response["truncated"] = True
            break
        untracked = status == "??"
        response["entries"].append(
            {
                "path": response_path(root / file_path),
                "status": status,
                "original_path": response_path(root / original_path) if original_path else None,
                "conflicted": "U" in status or status in {"AA", "DD"},
                "staged": not untracked and status[0] != " ",
                "unstaged": not untracked and status[1] != " ",
                "untracked": untracked,
            }
        )
    response.update(
        {
            "git_root": root_info["root_path"],
            "branch": root_info["branch"],
            "detached_head": root_info["detached_head"],
        }
    )
    return response


def git_diff() -> dict[str, Any]:
    path = normalize_path(PAYLOAD.get("path"))
    if not path:
        raise RuntimeIsDirectory("workspace root")
    target = resolve_container_path(path, must_exist=False)
    probe = target if target.exists() and target.is_dir() else target.parent
    while probe.parent != probe and not probe.exists():
        probe = probe.parent
    if not probe.exists():
        raise RuntimeNotFound(path)
    if target.exists() and not target.is_file():
        raise RuntimeIsDirectory(path)
    root = resolve_git_root(probe if probe.is_dir() else probe.parent)
    if root is None:
        raise RuntimePathError("Path is not inside a git repository")
    root_info = git_root_payload(root)
    file_rel = target.relative_to(root).as_posix()
    base_ref = str(PAYLOAD.get("base_ref", "HEAD")).strip()
    if base_ref.startswith("-"):
        raise RuntimePathError("Invalid comparison reference")
    context_lines = normalize_limit(PAYLOAD.get("context_lines"), 3, 0, 20)
    max_bytes = normalize_limit(PAYLOAD.get("max_bytes"), 120000, 1024, 500000)
    comparison = base_ref
    if base_ref == "HEAD":
        head_code, _, _ = run(["git", "-C", str(root), "rev-parse", "--verify", "HEAD"], timeout=4)
        if head_code:
            comparison = git_output(root, "hash-object", "-t", "tree", "/dev/null").strip()
    command = [
        "git",
        "--no-optional-locks",
        "-C",
        str(root),
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        f"--unified={context_lines}",
    ]
    if bool(PAYLOAD.get("staged")):
        command.append("--staged")
    if comparison:
        command.append(comparison)
    command.extend(["--", file_rel])
    code, stdout, stderr = run(command, timeout=8)
    if code != 0:
        raise RuntimePathError(stderr.strip() or stdout.strip() or "git diff failed")
    if not stdout and target.exists() and not bool(PAYLOAD.get("staged")):
        tracked_code, _tracked_stdout, _tracked_stderr = run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", file_rel], timeout=4
        )
        if tracked_code != 0:
            code, stdout, stderr = run(
                [
                    "git",
                    "-C",
                    str(root),
                    "diff",
                    "--no-index",
                    "--no-ext-diff",
                    "--no-textconv",
                    f"--unified={context_lines}",
                    "--",
                    "/dev/null",
                    file_rel,
                ],
                timeout=8,
            )
            if code not in (0, 1):
                raise RuntimePathError(stderr.strip() or "Could not compare untracked file")
    diff, truncated = truncate_utf8(stdout, max_bytes)
    response = workspace_exists_payload(path)
    response.update(
        {
            "git_root": root_info["root_path"],
            "branch": root_info["branch"],
            "detached_head": root_info["detached_head"],
            "repository": root_info,
            "base_ref": base_ref,
            "staged": bool(PAYLOAD.get("staged")),
            "context_lines": context_lines,
            "diff": diff,
            "truncated": truncated,
            "max_bytes": max_bytes,
        }
    )
    return response
