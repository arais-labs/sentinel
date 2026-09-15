from __future__ import annotations

import shlex

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=64)
def load_guest_command(path: str) -> str:
    normalized = path.strip("/")
    if not normalized or ".." in normalized.split("/"):
        raise ValueError("remote command path must be a relative resource path")
    return (
        files("app.services.runtime.guest_commands")
        .joinpath(*normalized.split("/"))
        .read_text(encoding="utf-8")
    )


@lru_cache(maxsize=32)
def load_guest_python(path: str) -> str:
    """Bundle shared file helpers for python -c without installing guest packages.

    Each resource remains normal Python source with explicit imports. Only these
    private helper modules are supplied in memory; process transport owns lifetime.
    """
    dependencies = []
    if path in {"common/files/operations.py", "common/files/read.py"}:
        dependencies.append(("sentinel_guest_files", load_guest_command("common/files/context.py")))
    if path == "common/files/operations.py":
        dependencies.append(("sentinel_guest_git", load_guest_command("common/git/operations.py")))
    source = load_guest_command(path)
    if not dependencies:
        return source
    return (
        "import sys, types\n"
        f"for name, source in {dependencies!r}:\n"
        "    module = types.ModuleType(name)\n"
        "    sys.modules[name] = module\n"
        "    exec(compile(source, name, 'exec'), module.__dict__)\n"
        f"exec(compile({source!r}, {path!r}, 'exec'))\n"
    )


def guest_python_command(path: str, args: list[str]) -> str:
    return shlex.join(["python3", "-c", load_guest_python(path), *args])
