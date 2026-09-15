"""Read Codex CLI OAuth credentials without changing its store."""

import json
from typing import Any


def _strip_or_none(value):
    return value.strip() or None if isinstance(value, str) else None


def extract_codex_access_token(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Codex auth file is not valid JSON.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Codex auth file must contain a JSON object.")

    token = _find_codex_access_token(payload)
    if token is None:
        raise ValueError("Codex auth file does not contain an access_token.")
    return token


def _find_codex_access_token(payload: dict[str, Any]) -> str | None:
    priority_paths = (
        ("tokens", "access_token"),
        ("tokens", "accessToken"),
        ("auth", "access_token"),
        ("auth", "accessToken"),
        ("oauth", "access_token"),
        ("oauth", "accessToken"),
        ("access_token",),
        ("accessToken",),
        ("OPENAI_OAUTH_TOKEN",),
    )
    for path in priority_paths:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        token = _strip_or_none(current if isinstance(current, str) else None)
        if token is not None:
            return token

    return _find_nested_access_token(payload)


def _find_nested_access_token(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("access_token", "accessToken"):
            token = _strip_or_none(value.get(key) if isinstance(value.get(key), str) else None)
            if token is not None:
                return token
        for child in value.values():
            token = _find_nested_access_token(child)
            if token is not None:
                return token
    if isinstance(value, list):
        for child in value:
            token = _find_nested_access_token(child)
            if token is not None:
                return token
    return None
