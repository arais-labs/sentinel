"""OAuth account quotas, normalized for Settings; never inference/token estimates."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.config import Settings
from app.services.settings.settings_service import selected_provider_models
from sentral.llm.ids import ProviderChoice
from sentral.llm.providers.anthropic import AnthropicProvider
from sentral.llm.providers.codex import CodexProvider
from sentral.llm.providers.gemini_oauth import GeminiCodeAssistHTTPError, GeminiOAuthProvider

OAuthProvider = Literal["anthropic", "openai", "gemini"]
CACHE_SECONDS = 300


class UsageWindow(BaseModel):
    key: str
    label: str
    remaining_percent: float
    resets_at: datetime | None = None
    model_scope: str | None = None


class ProviderUsage(BaseModel):
    status: Literal["available", "unavailable", "not_connected"]
    windows: list[UsageWindow] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message: str | None = None


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _reset(value) -> datetime | None:
    try:
        if _number(value) is not None:
            return datetime.fromtimestamp(value, UTC)
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _window(key, label, remaining, reset, model_scope=None) -> UsageWindow | None:
    percent = _number(remaining)
    if percent is None or not 0 <= percent <= 100:
        return None
    return UsageWindow(
        key=key,
        label=label,
        remaining_percent=round(percent, 2),
        resets_at=_reset(reset),
        model_scope=model_scope,
    )


def normalize_usage(provider: OAuthProvider, data: dict) -> list[UsageWindow]:
    windows = []
    if not isinstance(data, dict):
        return windows
    if provider == "anthropic":
        for key, label in (
            ("seven_day", "Weekly"),
            ("five_hour", "5 hours"),
            ("seven_day_sonnet", "Sonnet · Weekly"),
            ("seven_day_opus", "Opus · Weekly"),
        ):
            value = data.get(key)
            if isinstance(value, dict) and (used := _number(value.get("utilization"))) is not None:
                scope = {"seven_day_sonnet": "sonnet", "seven_day_opus": "opus"}.get(key)
                windows.append(_window(key, label, 100 - used, value.get("resets_at"), scope))
        # Newer Claude responses name model-specific limits (including Fable)
        # in `limits`; opaque top-level rollout fields are not reliable labels.
        limits = data.get("limits")
        for index, limit in enumerate(limits if isinstance(limits, list) else []):
            if not isinstance(limit, dict) or (used := _number(limit.get("percent"))) is None:
                continue
            kind = limit.get("kind")
            if not isinstance(kind, str):
                continue
            label = {"session": "5 hours", "weekly_all": "Weekly"}.get(kind)
            model_scope = None
            if kind == "weekly_scoped":
                scope = limit.get("scope")
                model = scope.get("model") if isinstance(scope, dict) else None
                name = model.get("display_name") if isinstance(model, dict) else None
                if isinstance(name, str) and name:
                    label = f"{name} · Weekly"
                    identifier = model.get("id")
                    model_scope = (
                        identifier if isinstance(identifier, str) and identifier else name.lower()
                    )
            if not label:
                continue
            window = _window(
                f"limit_{index}", label, 100 - used, limit.get("resets_at"), model_scope
            )
            if window:
                windows = [item for item in windows if item is not None and item.label != label]
                windows.append(window)
        windows.sort(key=lambda item: {"Weekly": 0, "5 hours": 1}.get(item.label, 2) if item else 3)
    elif provider == "openai":
        buckets = [("codex", "", data.get("rate_limit"), None)]
        additional = data.get("additional_rate_limits")
        if isinstance(additional, list):
            for index, bucket in enumerate(additional):
                if isinstance(bucket, dict):
                    label = bucket.get("limit_name")
                    if label == "gpt-reserve":
                        model = bucket.get("normal_model_slug")
                        label = (
                            f"GPT-{model[4:].replace('-', ' ').title()} reserve"
                            if isinstance(model, str) and model.startswith("gpt-")
                            else "Reserve"
                        )
                    buckets.append(
                        (
                            f"extra_{index}",
                            label if isinstance(label, str) else "",
                            bucket.get("rate_limit"),
                            (
                                bucket.get("normal_model_slug")
                                if isinstance(bucket.get("normal_model_slug"), str)
                                else None
                            ),
                        )
                    )
        for key, prefix, bucket, model_scope in buckets:
            if not isinstance(bucket, dict):
                continue
            for window_name in ("primary_window", "secondary_window"):
                value = bucket.get(window_name)
                if (
                    not isinstance(value, dict)
                    or (used := _number(value.get("used_percent"))) is None
                ):
                    continue
                seconds = _number(value.get("limit_window_seconds"))
                label = {604800: "Weekly", 18000: "5 hours", 86400: "Daily"}.get(
                    seconds, "Usage limit"
                )
                if prefix:
                    label = f"{prefix} · {label}"
                windows.append(
                    _window(
                        f"{key}_{window_name}",
                        label,
                        100 - used,
                        value.get("reset_at"),
                        model_scope,
                    )
                )
    else:
        # Google reports per-model buckets, not a guaranteed weekly window.
        buckets = data.get("buckets")
        for index, bucket in enumerate(buckets if isinstance(buckets, list) else []):
            if (
                not isinstance(bucket, dict)
                or (fraction := _number(bucket.get("remainingFraction"))) is None
            ):
                continue
            model = bucket.get("modelId")
            label = model if isinstance(model, str) and model else "Model quota"
            token_type = bucket.get("tokenType")
            if isinstance(token_type, str) and token_type:
                label = f"{label} · {token_type.lower()}"
            windows.append(
                _window(
                    f"bucket_{index}",
                    label,
                    fraction * 100,
                    bucket.get("resetTime"),
                    model if isinstance(model, str) and model else "unknown",
                )
            )
    return [window for window in windows if window is not None]


def select_model_usage(
    usage: ProviderUsage, provider: OAuthProvider, config: Settings
) -> ProviderUsage:
    """Project an account snapshot without modifying the credential-scoped cache.

    Match only explicit identities or the adapter's existing alias resolution;
    equal percentages do not establish that two models share a quota.
    """
    selected = list(
        dict.fromkeys(selected_provider_models(config, ProviderChoice(provider)).values())
    )

    def matches(model: str, scope: str) -> bool:
        if provider == "gemini":
            return GeminiOAuthProvider.resolve_model_id(
                model
            ) == GeminiOAuthProvider.resolve_model_id(scope)
        if provider == "anthropic" and scope in ("sonnet", "opus", "haiku", "fable", "mythos"):
            return (
                model.startswith(f"claude-{scope}-")
                or model.startswith(f"claude-3-5-{scope}-")
                or model.startswith(f"claude-3-7-{scope}-")
            )
        return model == scope

    result = usage.model_copy(deep=True)
    result.windows = [
        window
        for window in result.windows
        if window.model_scope is None
        or any(matches(model, window.model_scope) for model in selected)
    ]
    if provider == "gemini":
        # Prefer the exact ID sent by the adapter. Aliases are only a fallback,
        # not additional limits for the same selected model (e.g. pro-agent).
        scopes = list(dict.fromkeys(window.model_scope for window in result.windows))
        preferred = set()
        for model in selected:
            resolved = GeminiOAuthProvider.resolve_model_id(model)
            scope = (
                resolved
                if resolved in scopes
                else (
                    model
                    if model in scopes
                    else next((scope for scope in scopes if scope and matches(model, scope)), None)
                )
            )
            if scope:
                preferred.add(scope)
        result.windows = [
            window
            for window in result.windows
            if window.model_scope is None or window.model_scope in preferred
        ]
    return result


class ProviderUsageService:
    def __init__(self, *, client_factory=None):
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=8))
        self._cache: OrderedDict[tuple[str, str], tuple[float, ProviderUsage]] = OrderedDict()
        self._locks = {name: asyncio.Lock() for name in ("anthropic", "openai", "gemini")}

    async def get_usage(self, provider: OAuthProvider, settings: Settings) -> ProviderUsage:
        credential = getattr(
            settings,
            f"{provider}_oauth_credentials" if provider == "gemini" else f"{provider}_oauth_token",
        )
        if not credential:
            return ProviderUsage(status="not_connected", message="Connect OAuth to view usage.")
        # Account-scoped cache: no credentials or provider response bodies are retained.
        key = (provider, hashlib.sha256(credential.encode()).hexdigest())
        async with self._locks[provider]:
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                self._cache.move_to_end(key)
                return cached[1].model_copy(deep=True)
            result = await self._fetch(provider, credential)
            self._cache[key] = (time.monotonic() + CACHE_SECONDS, result)
            self._cache.move_to_end(key)
            while len(self._cache) > 128:
                self._cache.popitem(last=False)
            return result.model_copy(deep=True)

    async def _fetch(self, provider: OAuthProvider, credential: str) -> ProviderUsage:
        adapters = {
            "anthropic": AnthropicProvider,
            "openai": CodexProvider,
            "gemini": GeminiOAuthProvider,
        }
        try:
            async with asyncio.timeout(12):
                adapter = adapters[provider](credential, client_factory=self._client_factory)
                data = await adapter.get_account_usage()
            windows = normalize_usage(provider, data)
            if windows:
                return ProviderUsage(status="available", windows=windows)
            message = "Usage is not provided for this account."
        except (httpx.HTTPStatusError, GeminiCodeAssistHTTPError) as error:
            status = (
                error.response.status_code
                if isinstance(error, httpx.HTTPStatusError)
                else error.status_code
            )
            message = (
                "Reconnect OAuth to view usage."
                if status in (401, 403)
                else (
                    "Usage is temporarily rate-limited."
                    if status == 429
                    else "Usage is temporarily unavailable."
                )
            )
        except (httpx.RequestError, TimeoutError, ValueError, RuntimeError):
            # Do not expose upstream exception text, bodies, or credential validation inputs.
            message = "Usage is temporarily unavailable."
        return ProviderUsage(status="unavailable", message=message)


provider_usage_service = ProviderUsageService()
