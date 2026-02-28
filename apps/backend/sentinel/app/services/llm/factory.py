from __future__ import annotations

from app.schemas.models import ModelOptionResponse, ModelsResponse
from app.services.llm import AnthropicProvider, CodexProvider, GeminiProvider, OpenAIProvider
from app.services.llm.generic.base import LLMProvider
from app.services.llm.generic.tier import TierConfig, TierModelConfig, TierProvider
from app.services.llm.generic.types import ReasoningConfig
from app.services.llm.ids import ProviderChoice, TierName, parse_provider_choice
from app.config import Settings

DEFAULT_TIER_NAME = TierName.NORMAL

def build_models_response(provider: object | None) -> ModelsResponse:
    if provider is None:
        return ModelsResponse(models=[], default_tier=None)

    available_tiers = getattr(provider, "available_tiers", None)
    if not callable(available_tiers):
        return ModelsResponse(models=[], default_tier=None)

    models: list[ModelOptionResponse] = []
    for item in available_tiers():
        if isinstance(item, ModelOptionResponse):
            models.append(item)
        else:
            models.append(ModelOptionResponse.model_validate(item))
    default_tier = DEFAULT_TIER_NAME if models else None
    return ModelsResponse(models=models, default_tier=default_tier)


def build_tier_provider_from_settings(settings: Settings) -> LLMProvider | None:
    providers, openai_uses_codex = _build_enabled_providers(settings)
    if not providers:
        return None

    tiers: dict[TierName, TierConfig] = {}
    for (
        tier_name,
        anthropic_model,
        openai_model,
        codex_model,
        gemini_model,
        max_tokens,
        temperature,
        anthropic_thinking_budget,
        openai_reasoning_effort,
        gemini_thinking_budget,
    ) in _tier_rows(settings):
        candidates: dict[ProviderChoice, TierModelConfig] = {}

        anthropic = providers.get(ProviderChoice.ANTHROPIC)
        if anthropic is not None:
            candidates[ProviderChoice.ANTHROPIC] = TierModelConfig(
                provider=anthropic,
                model=anthropic_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tokens,
                    thinking_budget=anthropic_thinking_budget if anthropic_thinking_budget > 0 else None,
                ),
                temperature=temperature,
            )

        openai = providers.get(ProviderChoice.OPENAI)
        if openai is not None:
            candidates[ProviderChoice.OPENAI] = TierModelConfig(
                provider=openai,
                model=codex_model if openai_uses_codex else openai_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tokens,
                    reasoning_effort=openai_reasoning_effort or None,
                ),
                temperature=temperature,
            )

        gemini = providers.get(ProviderChoice.GEMINI)
        if gemini is not None:
            candidates[ProviderChoice.GEMINI] = TierModelConfig(
                provider=gemini,
                model=gemini_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tokens,
                    thinking_budget=gemini_thinking_budget if gemini_thinking_budget > 0 else None,
                ),
                temperature=temperature,
            )

        if not candidates:
            continue

        primary_name = parse_provider_choice(settings.primary_provider)
        if primary_name in candidates:
            primary = candidates[primary_name]
            fallbacks = [cfg for provider_name, cfg in candidates.items() if provider_name != primary_name]
        else:
            ordered = list(candidates.values())
            primary = ordered[0]
            fallbacks = ordered[1:]

        tiers[tier_name] = TierConfig(primary=primary, fallbacks=fallbacks)

    if not tiers:
        return None

    return TierProvider(
        tiers=tiers,
        default_tier=DEFAULT_TIER_NAME,
        max_retries=settings.llm_max_retries,
    )


def _build_enabled_providers(settings: Settings) -> tuple[dict[ProviderChoice, LLMProvider], bool]:
    providers: dict[ProviderChoice, LLMProvider] = {}

    anthropic_token = settings.anthropic_oauth_token or settings.anthropic_api_key
    if anthropic_token:
        providers[ProviderChoice.ANTHROPIC] = AnthropicProvider(anthropic_token)

    openai_uses_codex = False
    openai_oauth_token = settings.openai_oauth_token
    openai_api_key = settings.openai_api_key
    if openai_oauth_token:
        providers[ProviderChoice.OPENAI] = CodexProvider(openai_oauth_token)
        openai_uses_codex = True
    elif openai_api_key:
        providers[ProviderChoice.OPENAI] = OpenAIProvider(
            openai_api_key,
            base_url=settings.openai_base_url,
        )

    gemini_api_key = settings.gemini_api_key
    if gemini_api_key:
        providers[ProviderChoice.GEMINI] = GeminiProvider(gemini_api_key)

    return providers, openai_uses_codex


def _tier_rows(
    settings: Settings,
) -> tuple[tuple[TierName, str, str, str, str, int, float, int, str, int], ...]:
    return (
        (
            TierName.FAST,
            settings.tier_fast_anthropic_model,
            settings.tier_fast_openai_model,
            settings.tier_fast_codex_model,
            settings.tier_fast_gemini_model,
            settings.tier_fast_max_tokens,
            settings.tier_fast_temperature,
            settings.tier_fast_anthropic_thinking_budget,
            settings.tier_fast_openai_reasoning_effort,
            settings.tier_fast_gemini_thinking_budget,
        ),
        (
            TierName.NORMAL,
            settings.tier_normal_anthropic_model,
            settings.tier_normal_openai_model,
            settings.tier_normal_codex_model,
            settings.tier_normal_gemini_model,
            settings.tier_normal_max_tokens,
            settings.tier_normal_temperature,
            settings.tier_normal_anthropic_thinking_budget,
            settings.tier_normal_openai_reasoning_effort,
            settings.tier_normal_gemini_thinking_budget,
        ),
        (
            TierName.HARD,
            settings.tier_hard_anthropic_model,
            settings.tier_hard_openai_model,
            settings.tier_hard_codex_model,
            settings.tier_hard_gemini_model,
            settings.tier_hard_max_tokens,
            settings.tier_hard_temperature,
            settings.tier_hard_anthropic_thinking_budget,
            settings.tier_hard_openai_reasoning_effort,
            settings.tier_hard_gemini_thinking_budget,
        ),
    )
