from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.services.llm.base import LLMProvider
from app.services.llm.types import AgentEvent, AgentMessage, AssistantMessage, ReasoningConfig, ToolSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

_TIER_LABELS: dict[str, tuple[str, str]] = {
    "fast": ("Fast", "Quick responses, minimal reasoning"),
    "normal": ("Normal", "Balanced quality and speed"),
    "hard": ("Deep Think", "Extended reasoning for complex problems"),
}

# Backward-compat hint aliases → canonical tier name
_HINT_TO_TIER: dict[str, str] = {
    "fast": "fast",
    "normal": "normal",
    "hard": "hard",
    "reasoning": "normal",
    "anthropic": "normal",
}


@dataclass(slots=True)
class TierModelConfig:
    """One provider's config within a tier."""
    provider: LLMProvider
    model: str
    reasoning_config: ReasoningConfig
    temperature: float = 0.7


@dataclass(slots=True)
class TierConfig:
    """A tier has a primary and zero or more fallback providers."""
    primary: TierModelConfig
    fallbacks: list[TierModelConfig] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cooldown tracker  (inspired by OpenClaw)
# ---------------------------------------------------------------------------

@dataclass
class _CooldownState:
    """Tracks when a provider was last rate-limited."""
    cooldown_until: float = 0.0          # time.monotonic() deadline
    last_probe: float = 0.0             # last time we probed during cooldown
    cooldown_seconds: float = 60.0      # window length
    probe_interval_seconds: float = 30.0  # how often to probe recovery


# ---------------------------------------------------------------------------
# TierProvider
# ---------------------------------------------------------------------------

class TierProvider(LLMProvider):
    """Routes hint:<tier> model strings to per-tier primary/fallback providers.

    Each tier carries its own model names and ReasoningConfig, so fallback
    never sends the wrong model string to the wrong provider.
    """

    def __init__(
        self,
        tiers: dict[str, TierConfig],
        *,
        default_tier: str = "normal",
        max_retries: int = 3,
        base_backoff_ms: int = 500,
        sleep_func: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        if not tiers:
            raise ValueError("TierProvider requires at least one tier")
        self._tiers = tiers
        self._default_tier = default_tier
        self._max_retries = max_retries
        self._base_backoff_ms = base_backoff_ms
        self._sleep = sleep_func
        # Per-provider cooldown tracking (keyed by provider.name)
        self._cooldowns: dict[str, _CooldownState] = {}

    @property
    def name(self) -> str:
        return "tier"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
    ) -> AssistantMessage:
        tier_cfg = self._resolve_tier(model)
        return await self._call_with_fallback(
            tier_cfg,
            method="chat",
            messages=messages,
            tools=tools,
        )

    async def stream(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
    ) -> AsyncIterator[AgentEvent]:
        tier_cfg = self._resolve_tier(model)
        async for event in self._stream_with_fallback(
            tier_cfg,
            messages=messages,
            tools=tools,
        ):
            yield event

    def available_tiers(self) -> list[dict[str, Any]]:
        """Return model descriptors for the /models API."""
        result: list[dict[str, Any]] = []
        for tier_name, tier_cfg in self._tiers.items():
            label, description = _TIER_LABELS.get(tier_name, (tier_name.title(), ""))
            entry: dict[str, Any] = {
                "id": f"hint:{tier_name}",
                "label": label,
                "description": description,
                "tier": tier_name,
                "primary_provider": tier_cfg.primary.provider.name,
                "primary_model": tier_cfg.primary.model,
            }
            if tier_cfg.fallbacks:
                entry["fallback_providers"] = [
                    {"provider": fb.provider.name, "model": fb.model}
                    for fb in tier_cfg.fallbacks
                ]
            # Expose reasoning params so UI can display them
            rc = tier_cfg.primary.reasoning_config
            if rc.thinking_budget and rc.thinking_budget > 0:
                entry["thinking_budget"] = rc.thinking_budget
            for fb in tier_cfg.fallbacks:
                frc = fb.reasoning_config
                if frc.reasoning_effort:
                    entry["reasoning_effort"] = frc.reasoning_effort
                    break
            result.append(entry)
        return result

    # ------------------------------------------------------------------
    # Tier resolution
    # ------------------------------------------------------------------

    def _resolve_tier(self, model: str) -> TierConfig:
        if model.startswith("hint:"):
            hint = model.split(":", 1)[1]
            tier_name = _HINT_TO_TIER.get(hint, self._default_tier)
        else:
            tier_name = self._default_tier
        return self._tiers.get(tier_name) or self._tiers[self._default_tier]

    # ------------------------------------------------------------------
    # Cooldown helpers
    # ------------------------------------------------------------------

    def _get_cooldown(self, provider_name: str) -> _CooldownState:
        if provider_name not in self._cooldowns:
            self._cooldowns[provider_name] = _CooldownState()
        return self._cooldowns[provider_name]

    def _is_cooled_down(self, provider_name: str) -> bool:
        cd = self._get_cooldown(provider_name)
        return time.monotonic() < cd.cooldown_until

    def _should_probe(self, provider_name: str) -> bool:
        cd = self._get_cooldown(provider_name)
        now = time.monotonic()
        if now >= cd.cooldown_until:
            return True  # cooldown expired
        return (now - cd.last_probe) >= cd.probe_interval_seconds

    def _record_rate_limit(self, provider_name: str) -> None:
        cd = self._get_cooldown(provider_name)
        cd.cooldown_until = time.monotonic() + cd.cooldown_seconds
        logger.warning("Provider %s rate-limited, cooldown until +%.0fs", provider_name, cd.cooldown_seconds)

    def _record_probe(self, provider_name: str) -> None:
        cd = self._get_cooldown(provider_name)
        cd.last_probe = time.monotonic()

    def _clear_cooldown(self, provider_name: str) -> None:
        cd = self._get_cooldown(provider_name)
        cd.cooldown_until = 0.0

    # ------------------------------------------------------------------
    # Execution with fallback + retry
    # ------------------------------------------------------------------

    def _ordered_configs(self, tier: TierConfig) -> list[TierModelConfig]:
        """Return [primary, ...fallbacks] ordered by cooldown state.

        If primary is cooled down and it's not time to probe yet,
        move it to the end so fallbacks are tried first.
        """
        configs = [tier.primary, *tier.fallbacks]

        primary_name = tier.primary.provider.name
        if self._is_cooled_down(primary_name) and not self._should_probe(primary_name):
            if tier.fallbacks:
                configs = [*tier.fallbacks, tier.primary]
        return configs

    async def _call_with_fallback(
        self,
        tier: TierConfig,
        *,
        method: str,
        messages: Sequence[AgentMessage | dict],
        tools: Sequence[ToolSchema] | None,
    ) -> AssistantMessage:
        configs = self._ordered_configs(tier)
        diagnostics: list[str] = []

        for cfg in configs:
            pname = cfg.provider.name
            if self._is_cooled_down(pname) and cfg is not configs[-1]:
                if not self._should_probe(pname):
                    continue
                self._record_probe(pname)

            for attempt in range(1, self._max_retries + 1):
                try:
                    result = await cfg.provider.chat(
                        messages,
                        model=cfg.model,
                        tools=tools,
                        temperature=cfg.temperature,
                        reasoning_config=cfg.reasoning_config,
                    )
                    self._clear_cooldown(pname)
                    return result
                except Exception as exc:  # noqa: BLE001
                    retryable = _is_retryable(exc)
                    if _status_code(exc) == 429:
                        self._record_rate_limit(pname)
                    diagnostics.append(
                        f"provider={pname} model={cfg.model} attempt {attempt}/{self._max_retries}: {_error_tag(exc)}"
                    )
                    if retryable and attempt < self._max_retries:
                        await self._sleep((self._base_backoff_ms * (2 ** (attempt - 1))) / 1000)
                        continue
                    break  # move to next config

        raise RuntimeError("All providers failed. " + " | ".join(diagnostics))

    async def _stream_with_fallback(
        self,
        tier: TierConfig,
        *,
        messages: Sequence[AgentMessage | dict],
        tools: Sequence[ToolSchema] | None,
    ) -> AsyncIterator[AgentEvent]:
        configs = self._ordered_configs(tier)
        diagnostics: list[str] = []

        for cfg in configs:
            pname = cfg.provider.name
            if self._is_cooled_down(pname) and cfg is not configs[-1]:
                if not self._should_probe(pname):
                    continue
                self._record_probe(pname)

            for attempt in range(1, self._max_retries + 1):
                try:
                    async for event in cfg.provider.stream(
                        messages,
                        model=cfg.model,
                        tools=tools,
                        temperature=cfg.temperature,
                        reasoning_config=cfg.reasoning_config,
                    ):
                        yield event
                    self._clear_cooldown(pname)
                    return
                except Exception as exc:  # noqa: BLE001
                    retryable = _is_retryable(exc)
                    if _status_code(exc) == 429:
                        self._record_rate_limit(pname)
                    diagnostics.append(
                        f"provider={pname} model={cfg.model} attempt {attempt}/{self._max_retries}: {_error_tag(exc)}"
                    )
                    if retryable and attempt < self._max_retries:
                        await self._sleep((self._base_backoff_ms * (2 ** (attempt - 1))) / 1000)
                        continue
                    break

        raise RuntimeError("All providers failed. " + " | ".join(diagnostics))


# ---------------------------------------------------------------------------
# Error helpers (shared with reliable_provider.py — could be factored later)
# ---------------------------------------------------------------------------

def _status_code(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return getattr(exc, "status_code", None)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
        return True
    status_code = _status_code(exc)
    if status_code is None:
        return False
    if status_code == 429:
        return True
    return status_code >= 500


def _error_tag(exc: Exception) -> str:
    status_code = _status_code(exc)
    if status_code == 429:
        return "rate_limited"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, (ConnectionError, httpx.ConnectError, httpx.NetworkError)):
        return "connection_error"
    if status_code is not None:
        body = ""
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                body = (exc.response.text or "")[:300]
            except Exception:  # noqa: BLE001
                body = "<streaming body unavailable>"
        return f"http_{status_code}: {body}".strip()
    return f"{exc.__class__.__name__}: {exc}"
