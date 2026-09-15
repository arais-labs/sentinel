"""Tiered provider routing with cooldown-based failover.

Maps tier names (fast/normal/hard) to per-provider model configs and retries.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.schemas.models import ModelFallbackResponse, ModelOptionResponse
from sentral.llm.generic.base import LLMProvider
from sentral.llm.generic.errors import error_tag, is_retryable, status_code
from sentral.llm.generic.types import (
    AgentEvent,
    AgentMessage,
    AssistantMessage,
    ReasoningConfig,
    ToolSchema,
)
from sentral.llm.ids import ProviderId, TierName, parse_tier_name
from sentral.llm.model_limits import model_context
from sentral.llm.tier_defaults import TIER_LABELS
from app.services.llm.session_selection import LEVELS, reasoning_levels, with_reasoning

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


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

    cooldown_until: float = 0.0  # time.monotonic() deadline
    last_probe: float = 0.0  # last time we probed during cooldown
    cooldown_seconds: float = 60.0  # window length
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

    def model_context(self, model):

        primary = self._resolve_tier(model).primary
        return model_context(primary.model, primary.reasoning_config.max_tokens)

    async def count_input_tokens(self, messages, model, tools=None, reasoning_config=None):
        primary = self._resolve_tier(model).primary
        return await primary.provider.count_input_tokens(
            messages, primary.model, tools, primary.reasoning_config
        )

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
            reasoning_config=reasoning_config,
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
            reasoning_config=reasoning_config,
        ):
            yield event

    def available_tiers(self) -> list[ModelOptionResponse]:
        """Return model descriptors for the /models API."""
        result: list[ModelOptionResponse] = []
        for tier_name, tier_cfg in self._tiers.items():
            label, description = TIER_LABELS.get(tier_name, (tier_name.value.title(), ""))
            rc = tier_cfg.primary.reasoning_config
            thinking_budget = (
                rc.thinking_budget if rc.thinking_budget and rc.thinking_budget > 0 else None
            )

            fallback_providers = [
                ModelFallbackResponse(
                    provider_id=self._required_provider_id(fb.provider),
                    model=fb.model,
                )
                for fb in tier_cfg.fallbacks
            ]

            result.append(
                ModelOptionResponse(
                    provider_options=[
                        self._session_option(c) for c in [tier_cfg.primary, *tier_cfg.fallbacks]
                    ],
                    label=label,
                    description=description,
                    tier=tier_name,
                    primary_provider_id=self._required_provider_id(tier_cfg.primary.provider),
                    primary_model_id=tier_cfg.primary.model,
                    context_window_tokens=self.model_context(tier_name)["context_window_tokens"],
                    context_token_budget=self.model_context(tier_name)["context_token_budget"],
                    output_reserve_tokens=self.model_context(tier_name)["output_reserve_tokens"],
                    fallback_providers=fallback_providers,
                    thinking_budget=thinking_budget,
                    reasoning_effort=rc.reasoning_effort or None,
                )
            )
        return result

    @staticmethod
    def _session_option(config):

        return {
            "provider_id": config.provider.provider_id,
            "model": config.model,
            "reasoning_levels": reasoning_levels(config.model),
            "supports_fast_mode": config.provider.supports_fast_mode(config.model),
            "reasoning_effort": config.reasoning_config.reasoning_effort,
            **model_context(config.model, config.reasoning_config.max_tokens),
        }

    def resolve_generation_hint(self, model: str) -> tuple[str, str] | None:
        """Expose the first concrete provider/model this request will attempt."""
        try:
            tier_cfg = self._resolve_tier(model)
        except Exception:  # noqa: BLE001
            return super().resolve_generation_hint(model)
        ordered = self._ordered_configs(tier_cfg)
        if not ordered:
            return super().resolve_generation_hint(model)
        primary = ordered[0]
        resolved_model = primary.model.strip() if isinstance(primary.model, str) else ""
        if not resolved_model:
            return super().resolve_generation_hint(model)
        return primary.provider.name, resolved_model

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
        if model.startswith("sentinel:"):

            parts = model.split(":")
            if len(parts) not in (4, 5) or (len(parts) == 5 and parts[4] != "fast"):
                raise ValueError("Invalid model selection")
            _, tier, provider, level = parts[:4]
            fast_mode = len(parts) == 5
            cfg = self._tiers[TierName(tier)]
            candidates = [cfg.primary, *cfg.fallbacks]
            if provider != "auto":
                candidates = [c for c in candidates if c.provider.provider_id == provider]
                if not candidates:
                    raise ValueError("Selected provider is not configured")
            if fast_mode:
                if not candidates[0].provider.supports_fast_mode(candidates[0].model):
                    raise ValueError("Fast mode is not supported by this provider and model")
                candidates = [
                    replace(c, reasoning_config=replace(c.reasoning_config, fast_mode=True))
                    for c in candidates
                    if c.provider.supports_fast_mode(c.model)
                ]
            if level != "default":
                if level not in LEVELS:
                    raise ValueError("Unknown reasoning level")
                candidates = [with_reasoning(c, level) for c in candidates]
            return TierConfig(primary=candidates[0], fallbacks=candidates[1:])
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
        logger.warning(
            "Provider %s rate-limited, cooldown until +%.0fs",
            provider_name,
            cd.cooldown_seconds,
        )

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
        reasoning_config: ReasoningConfig | None = None,
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
                        reasoning_config=(
                            replace(
                                cfg.reasoning_config,
                                **{
                                    key: getattr(reasoning_config, key)
                                    for key in reasoning_config.override_fields
                                },
                            )
                            if reasoning_config and reasoning_config.override_fields
                            else reasoning_config or cfg.reasoning_config
                        ),
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
        reasoning_config: ReasoningConfig | None = None,
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
                    attached_generation_hint = False
                    async for event in cfg.provider.stream(
                        messages,
                        model=cfg.model,
                        tools=tools,
                        temperature=cfg.temperature,
                        reasoning_config=(
                            replace(
                                cfg.reasoning_config,
                                **{
                                    key: getattr(reasoning_config, key)
                                    for key in reasoning_config.override_fields
                                },
                            )
                            if reasoning_config and reasoning_config.override_fields
                            else reasoning_config or cfg.reasoning_config
                        ),
                        tool_choice=tool_choice,
                    ):
                        if not attached_generation_hint:
                            attached_generation_hint = True
                            if event.message is None:
                                event.message = AssistantMessage(model=cfg.model, provider=pname)
                            else:
                                if not event.message.model:
                                    event.message.model = cfg.model
                                if not event.message.provider:
                                    event.message.provider = pname
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
