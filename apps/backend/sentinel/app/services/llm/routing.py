"""Provider card order and automatic participation, independent of credentials."""

from app.config import Settings
from sentral.llm.ids import ProviderChoice, parse_provider_choice


def provider_order(config: Settings) -> list[ProviderChoice]:
    if config.provider_order is not None:
        return [ProviderChoice(value) for value in config.provider_order]
    primary = parse_provider_choice(config.primary_provider) or ProviderChoice.ANTHROPIC
    legacy = [
        ProviderChoice.ANTHROPIC,
        ProviderChoice.OPENAI,
        ProviderChoice.GEMINI,
        ProviderChoice.OLLAMA,
    ]
    return [primary, *(provider for provider in legacy if provider != primary)]


def automatic_provider_order(config: Settings) -> list[ProviderChoice]:
    order = provider_order(config)
    if config.automatic_providers is None:
        return order
    return [provider for provider in order if provider in config.automatic_providers]
