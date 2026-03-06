from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.settings_service import SettingsService

_ARAIOS_TOKEN_REFRESH_BUFFER_SECONDS = 30


@dataclass(slots=True)
class _AraiOSTokenCacheEntry:
    access_token: str
    refresh_token: str | None
    expires_at: datetime


_araios_token_cache: dict[str, _AraiOSTokenCacheEntry] = {}
_araios_token_cache_lock = asyncio.Lock()


async def load_araios_runtime_credentials(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    settings_service = SettingsService()
    async with session_factory() as db:
        try:
            return await settings_service.get_araios_runtime_credentials(db)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


def join_araios_base_and_path(base_url: str, path: str) -> str:
    trimmed_path = path.strip()
    if not trimmed_path.startswith("/"):
        trimmed_path = f"/{trimmed_path}"
    return f"{base_url}{trimmed_path}"


async def araios_request_with_auth(
    *,
    client: httpx.AsyncClient,
    method: str,
    url: str,
    request_kwargs: dict[str, Any],
    base_url: str,
    agent_api_key: str,
) -> httpx.Response:
    access_token = await _get_araios_access_token(
        client=client,
        base_url=base_url,
        agent_api_key=agent_api_key,
    )

    first_headers = dict(request_kwargs.get("headers", {}))
    first_headers["Authorization"] = f"Bearer {access_token}"
    first_kwargs = dict(request_kwargs)
    first_kwargs["headers"] = first_headers

    response = await client.request(method, url, **first_kwargs)
    if response.status_code != 401:
        return response

    await _invalidate_araios_token_cache(base_url=base_url, agent_api_key=agent_api_key)
    retry_access_token = await _get_araios_access_token(
        client=client,
        base_url=base_url,
        agent_api_key=agent_api_key,
    )
    retry_headers = dict(request_kwargs.get("headers", {}))
    retry_headers["Authorization"] = f"Bearer {retry_access_token}"
    retry_kwargs = dict(request_kwargs)
    retry_kwargs["headers"] = retry_headers
    return await client.request(method, url, **retry_kwargs)


def _araios_cache_key(base_url: str, agent_api_key: str) -> str:
    digest = hashlib.sha256(agent_api_key.encode("utf-8")).hexdigest()
    return f"{base_url}|{digest}"


def _is_araios_token_fresh(entry: _AraiOSTokenCacheEntry) -> bool:
    refresh_deadline = datetime.now(UTC) + timedelta(seconds=_ARAIOS_TOKEN_REFRESH_BUFFER_SECONDS)
    return entry.expires_at > refresh_deadline


async def _get_araios_access_token(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    agent_api_key: str,
) -> str:
    cache_key = _araios_cache_key(base_url, agent_api_key)

    cached: _AraiOSTokenCacheEntry | None
    async with _araios_token_cache_lock:
        cached = _araios_token_cache.get(cache_key)
    if cached is not None and _is_araios_token_fresh(cached):
        return cached.access_token

    refreshed: _AraiOSTokenCacheEntry | None = None
    if cached is not None and cached.refresh_token:
        refreshed = await _refresh_araios_tokens(
            client=client,
            base_url=base_url,
            refresh_token=cached.refresh_token,
        )

    next_tokens = refreshed or await _issue_araios_tokens(
        client=client,
        base_url=base_url,
        agent_api_key=agent_api_key,
    )
    async with _araios_token_cache_lock:
        _araios_token_cache[cache_key] = next_tokens
    return next_tokens.access_token


async def _invalidate_araios_token_cache(*, base_url: str, agent_api_key: str) -> None:
    cache_key = _araios_cache_key(base_url, agent_api_key)
    async with _araios_token_cache_lock:
        _araios_token_cache.pop(cache_key, None)


async def _issue_araios_tokens(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    agent_api_key: str,
) -> _AraiOSTokenCacheEntry:
    token_url = f"{base_url}/platform/auth/token"
    try:
        response = await client.post(token_url, json={"api_key": agent_api_key})
    except httpx.HTTPError as exc:
        raise ValueError(f"AraiOS token exchange failed: {exc}") from exc
    if response.status_code != 200:
        raise ValueError(
            f"AraiOS token exchange failed with status {response.status_code}"
        )
    return _parse_araios_token_response(response)


async def _refresh_araios_tokens(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    refresh_token: str,
) -> _AraiOSTokenCacheEntry | None:
    refresh_url = f"{base_url}/platform/auth/refresh"
    try:
        response = await client.post(refresh_url, json={"refresh_token": refresh_token})
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        return _parse_araios_token_response(response)
    except ValueError:
        return None


def _parse_araios_token_response(response: httpx.Response) -> _AraiOSTokenCacheEntry:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("AraiOS token response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("AraiOS token response must be an object")
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise ValueError("AraiOS token response is missing access_token")

    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        refresh_token = None

    expires_in_raw = payload.get("expires_in")
    if (
        not isinstance(expires_in_raw, int)
        or isinstance(expires_in_raw, bool)
        or expires_in_raw <= 0
    ):
        expires_in_raw = 3600
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_raw)

    return _AraiOSTokenCacheEntry(
        access_token=access_token.strip(),
        refresh_token=refresh_token.strip() if isinstance(refresh_token, str) else None,
        expires_at=expires_at,
    )
