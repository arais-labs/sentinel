from __future__ import annotations

import hashlib
import json
from typing import Any


def normalize_command(value: str) -> str:
    return " ".join(value.strip().split()).lower()


def build_runtime_exec_match_key(*, command: str, privilege: str | None = None) -> str:
    normalized = normalize_command(command)
    scope = (privilege or "").strip().lower()
    if scope == "root":
        return f"runtime_exec:root:{normalized}"
    return f"runtime_exec:{normalized}"


def build_tool_match_key(
    *,
    tool_name: str,
    payload: dict[str, Any],
    explicit: str | None = None,
) -> str:
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    if tool_name == "runtime_exec":
        shell_command = payload.get("shell_command")
        if isinstance(shell_command, str) and shell_command.strip():
            privilege_raw = payload.get("privilege")
            privilege = privilege_raw if isinstance(privilege_raw, str) else None
            return build_runtime_exec_match_key(
                command=shell_command,
                privilege=privilege,
            )

    if tool_name == "git_exec":
        cli_command = payload.get("cli_command")
        if isinstance(cli_command, str) and cli_command.strip():
            return f"{tool_name}:{normalize_command(cli_command)}"

    command = payload.get("command")
    if isinstance(command, str) and command.strip():
        return f"{tool_name}:{normalize_command(command)}"

    canonical = _canonical_payload(payload)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{tool_name}:sha256:{digest}"


def _canonical_payload(payload: dict[str, Any]) -> str:
    sanitized = {
        key: value
        for key, value in payload.items()
        if key != "session_id" and not key.startswith("__")
    }
    try:
        return json.dumps(sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    except TypeError:
        return json.dumps(str(sanitized), ensure_ascii=True)
