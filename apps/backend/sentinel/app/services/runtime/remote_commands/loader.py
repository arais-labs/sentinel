from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=64)
def load_remote_command(path: str) -> str:
    normalized = path.strip("/")
    if not normalized or ".." in normalized.split("/"):
        raise ValueError("remote command path must be a relative resource path")
    return (
        files("app.services.runtime.remote_commands")
        .joinpath(*normalized.split("/"))
        .read_text(encoding="utf-8")
    )
