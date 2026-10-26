from __future__ import annotations

import os
from pathlib import Path


def list_local_directories(path: str) -> dict:
    """Browse the local machine without launching a shell or loading profiles."""
    directory = Path(path) if path else Path.home()
    directory = directory.resolve(strict=True)
    with os.scandir(directory) as entries:
        names = [entry.name for entry in entries if entry.is_dir()]
    return {
        "path": str(directory),
        "parent": str(directory.parent),
        "directories": sorted(names, key=str.casefold),
    }


def validate_new_directory(path: str, name: str) -> None:
    if not path.startswith("/") or "\0" in path:
        raise ValueError("Choose an absolute parent directory")
    if (
        not name.strip()
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 for char in name)
    ):
        raise ValueError("Enter a folder name without slashes or control characters")


def create_local_directory(path: str, name: str) -> dict:
    validate_new_directory(path, name)
    parent = Path(path).resolve(strict=True)
    directory = parent / name
    directory.mkdir()  # No parents or exist_ok: never reuse or overwrite a file.
    return {"path": str(directory), "parent": str(parent), "directories": []}
