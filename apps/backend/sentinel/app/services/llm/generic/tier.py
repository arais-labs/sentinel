"""Tiered provider routing with cooldown-based failover.

Maps tier names (fast/normal/hard) to per-provider model configs and retries.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.schemas.models import ModelFallbackResponse, ModelOptionResponse
from app.services.llm.ids import ProviderId, TierName, parse_tier_name
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.errors import error_tag, is_retryable, status_code
from app.services.llm.generic.types import AgentEvent, AgentMessage, AssistantMessage, ReasoningConfig, ToolSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

_TIER_LABELS: dict[TierName, tuple[str, str]] = {
    TierName.FAST: ("Fast", "Quick responses, minimal reasoning"),
    TierName.NORMAL: ("Normal", "Balanced quality and speed"),
    TierName.HARD: ("Deep Think", "Extended reasoning for complex problems"),
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
    """Routes tier values (for example ``normal``) to per-tier provider configs.

    Each tier carries its own model names and ReasoningConfig, so fallback
    never sends the wrong model string to the wrong provider.

    For non-tier model IDs (for example `gemini-2.5-flash`), routing is
    explicit:
    1) If the model matches a configured provider model, use that provider.
    2) Otherwise, pass the raw model through to the default tier primary
       provider.
    In both cases, fallbacks are disabled to avoid silently switching to a
    different model than requested.
    """

    def __init__(
        self,
        tiers: dict[TierName, TierConfig],
        *,
        default_tier: TierName = TierName.NORMAL,
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
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        tier_cfg = self._resolve_tier(model)
        return await self._call_with_fallback(
            tier_cfg,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )

    async def stream(
        self,
        messages: Sequence[AgentMessage | dict],
        model: str,
        tools: Sequence[ToolSchema] | None = None,
        temperature: float = 0.7,
        reasoning_config: ReasoningConfig | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        tier_cfg = self._resolve_tier(model)
        async for event in self._stream_with_fallback(
            tier_cfg,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        ):
            yield event

    def available_tiers(self) -> list[ModelOptionResponse]:
        """Return model descriptors for the /models API."""
        result: list[ModelOptionResponse] = []
        for tier_name, tier_cfg in self._tiers.items():
            label, description = _TIER_LABELS.get(tier_name, (tier_name.value.title(), ""))
            rc = tier_cfg.primary.reasoning_config
            thinking_budget = rc.thinking_budget if rc.thinking_budget and rc.thinking_budget > 0 else None

            fallback_providers = [
                ModelFallbackResponse(
                    provider_id=self._required_provider_id(fb.provider),
                    model=fb.model,
                )
                for fb in tier_cfg.fallbacks
            ]

            reasoning_effort: str | None = None
            for fb in tier_cfg.fallbacks:
                frc = fb.reasoning_config
                if frc.reasoning_effort:
                    reasoning_effort = frc.reasoning_effort
                    break

            result.append(
                ModelOptionResponse(
                    label=label,
                    description=description,
                    tier=tier_name,
                    primary_provider_id=self._required_provider_id(tier_cfg.primary.provider),
                    primary_model_id=tier_cfg.primary.model,
                    fallback_providers=fallback_providers,
                    thinking_budget=thinking_budget,
                    reasoning_effort=reasoning_effort,
                )
            )
        return result

    @staticmethod
    def _required_provider_id(provider: LLMProvider) -> ProviderId:
        provider_id = provider.provider_id
        if provider_id is None:
            raise ValueError(f"Provider {provider.name!r} is missing provider_id")
        return provider_id

    # ------------------------------------------------------------------
    # Tier resolution
    # ------------------------------------------------------------------

    def _resolve_tier(self, model: str) -> TierConfig:
        tier_name = parse_tier_name(model)
        if tier_name is not None:
            return self._tiers.get(tier_name) or self._tiers[self._default_tier]
        return self._resolve_non_tier_model(model)

    def _resolve_non_tier_model(self, model: str) -> TierConfig:
        default_tier = self._tiers.get(self._default_tier) or self._tiers[self._default_tier]
        match = self._find_model_config(default_tier, model)
        if match is not None:
            return TierConfig(primary=match, fallbacks=[])

        for tier in self._tiers.values():
            match = self._find_model_config(tier, model)
            if match is not None:
                return TierConfig(primary=match, fallbacks=[])

        passthrough = TierModelConfig(
            provider=default_tier.primary.provider,
            model=model,
            reasoning_config=default_tier.primary.reasoning_config,
            temperature=default_tier.primary.temperature,
        )
        return TierConfig(primary=passthrough, fallbacks=[])

    @staticmethod
    def _find_model_config(tier: TierConfig, model: str) -> TierModelConfig | None:
        configs = [tier.primary, *tier.fallbacks]
        for cfg in configs:
            if cfg.model == model:
                return cfg
        return None

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
        messages: Sequence[AgentMessage | dict],
        tools: Sequence[ToolSchema] | None,
        tool_choice: str | None = None,
    ) -> AssistantMessage:
        """Execute a non-streaming call through tier primary/fallback providers."""
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
                        tool_choice=tool_choice,
                    )
                    self._clear_cooldown(pname)
                    return result
                except Exception as exc:  # noqa: BLE001
                    retryable = is_retryable(exc)
                    if status_code(exc) == 429:
                        self._record_rate_limit(pname)
                    diagnostics.append(
                        f"provider={pname} model={cfg.model} attempt {attempt}/{self._max_retries}: {error_tag(exc)}"
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
        tool_choice: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Execute a streaming call through tier primary/fallback providers."""
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
                        tool_choice=tool_choice,
                    ):
                        yield event
                    self._clear_cooldown(pname)
                    return
                except Exception as exc:  # noqa: BLE001
                    retryable = is_retryable(exc)
                    if status_code(exc) == 429:
                        self._record_rate_limit(pname)
                    diagnostics.append(
                        f"provider={pname} model={cfg.model} attempt {attempt}/{self._max_retries}: {error_tag(exc)}"
                    )
                    if retryable and attempt < self._max_retries:
                        await self._sleep((self._base_backoff_ms * (2 ** (attempt - 1))) / 1000)
                        continue
                    break

        raise RuntimeError("All providers failed. " + " | ".join(diagnostics))
