from app.services.llm.factory import build_models_response
from sentral.llm.ids import ProviderId, TierName
import pytest


def test_automatic_order_excludes_disabled_providers_but_keeps_manual_choices():
    from app.config import Settings
    from app.services.llm.factory import build_tier_provider_from_settings

    config = Settings().model_copy(
        update={
            "anthropic_api_key": "test-key",
            "openai_api_key": "test-key",
            "gemini_api_key": "test-key",
            "ollama_base_url": "https://models.example",
            "ollama_model": "custom:4b",
            "provider_order": ["gemini", "ollama", "openai", "anthropic"],
            "automatic_providers": ["gemini", "openai"],
        }
    )
    runtime = build_tier_provider_from_settings(config)
    for tier in runtime.available_tiers():
        assert tier.primary_provider_id == ProviderId.GEMINI
        assert [item.provider_id for item in tier.fallback_providers] == [ProviderId.OPENAI]
        assert {item["provider_id"] for item in tier.provider_options} == set(ProviderId) - {
            ProviderId.OPENAI_CODEX
        }
    assert (
        runtime.resolve_model_config("sentinel:normal:anthropic:default").provider.provider_id
        == ProviderId.ANTHROPIC
    )
    assert runtime.resolve_model_config("sentinel:normal:ollama:default").model == "custom:4b"
    assert (
        runtime.resolve_model_config("sentinel:normal:auto:default").provider.provider_id
        == ProviderId.GEMINI
    )

    unavailable = config.model_copy(
        update={"automatic_providers": ["openai"], "openai_api_key": None}
    )
    runtime = build_tier_provider_from_settings(unavailable)
    assert len(runtime.available_tiers()[0].provider_options) == 3
    with pytest.raises(ValueError, match="No automatic provider"):
        runtime.resolve_model_config("normal")
    with pytest.raises(ValueError, match="No automatic provider"):
        runtime.resolve_model_config("sentinel:normal:auto:default")
    assert (
        runtime.resolve_model_config("sentinel:normal:gemini:default").provider.provider_id
        == ProviderId.GEMINI
    )


class _TierProviderStub:
    def __init__(self, tiers: list[dict[str, object]]) -> None:
        self._tiers = tiers

    def available_tiers(self) -> list[dict[str, object]]:
        return self._tiers


@pytest.mark.parametrize(
    "provider,namespace,credentials",
    [
        ("anthropic", "anthropic", {"anthropic_api_key": "test-key"}),
        ("openai", "openai", {"openai_api_key": "test-key"}),
        ("openai", "codex", {"openai_oauth_token": "test-token"}),
        ("gemini", "gemini", {"gemini_api_key": "test-key"}),
        (
            "ollama",
            "ollama",
            {"ollama_base_url": "https://models.example", "ollama_model": "default:4b"},
        ),
    ],
)
def test_saved_tier_models_drive_the_runtime_catalog(provider, namespace, credentials):
    from app.config import Settings
    from app.services.llm.factory import build_tier_provider_from_settings

    models = {
        f"tier_{tier}_{namespace}_model": f"custom-{tier}" for tier in ("fast", "normal", "hard")
    }
    config = Settings().model_copy(update={**credentials, **models, "primary_provider": provider})
    runtime = build_tier_provider_from_settings(config)
    assert [entry.primary_model_id for entry in build_models_response(runtime).models] == [
        "custom-fast",
        "custom-normal",
        "custom-hard",
    ]
    assert [runtime.resolve_generation_hint(tier)[1] for tier in ("fast", "normal", "hard")] == [
        "custom-fast",
        "custom-normal",
        "custom-hard",
    ]


def test_build_models_response_uses_fallback_when_provider_missing():
    payload = build_models_response(None)
    assert payload.default_tier is None
    assert payload.models == []


def test_build_models_response_uses_provider_tiers_directly():
    provider = _TierProviderStub(
        [
            {
                "label": "Fast",
                "description": "Quick",
                "tier": "fast",
                "primary_provider_id": ProviderId.ANTHROPIC,
                "primary_model_id": "claude-haiku",
            },
            {
                "label": "Normal",
                "description": "Balanced",
                "tier": "normal",
                "primary_provider_id": ProviderId.ANTHROPIC,
                "primary_model_id": "claude-sonnet",
            },
            {
                "label": "Deep Think",
                "description": "Extended",
                "tier": "hard",
                "primary_provider_id": ProviderId.OPENAI,
                "primary_model_id": "o3",
            },
        ]
    )

    payload = build_models_response(provider)
    assert [model.tier for model in payload.models] == [
        TierName.FAST,
        TierName.NORMAL,
        TierName.HARD,
    ]


def test_build_models_response_passes_provider_entries_without_extra_aliases():
    provider = _TierProviderStub(
        [
            {
                "label": "Normal",
                "description": "Balanced",
                "tier": "normal",
                "primary_provider_id": ProviderId.ANTHROPIC,
                "primary_model_id": "claude-sonnet",
            },
        ]
    )

    payload = build_models_response(provider)
    assert [model.tier for model in payload.models] == [TierName.NORMAL]


def test_openai_catalog_offers_current_models_with_api_key_or_codex_login():
    from app.config import Settings
    from app.services.llm.factory import build_tier_provider_from_settings

    for credential in ("openai_api_key", "openai_oauth_token"):
        settings = Settings(_env_file=None).model_copy(
            update={credential: "test-credential", "primary_provider": "openai"}
        )
        provider = build_tier_provider_from_settings(settings)
        payload = build_models_response(provider)

        assert payload.default_tier == TierName.NORMAL
        assert [model.primary_model_id for model in payload.models] == [
            "gpt-6-luna",
            "gpt-6.1-sol",
            "gpt-6-astra",
        ]


def test_claude_catalog_offers_current_tiers_with_api_key_or_oauth():
    from app.config import Settings
    from app.services.llm.factory import build_tier_provider_from_settings

    for credential in ("anthropic_api_key", "anthropic_oauth_token"):
        settings = Settings(_env_file=None).model_copy(
            update={credential: "test-credential", "primary_provider": "anthropic"}
        )
        provider = build_tier_provider_from_settings(settings)
        payload = build_models_response(provider)
        assert payload.default_tier == TierName.NORMAL
        assert [model.primary_model_id for model in payload.models] == [
            "claude-sonnet-5-5",
            "claude-opus-5-5",
            "claude-fable-5-1",
        ]
