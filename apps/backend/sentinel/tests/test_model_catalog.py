from app.services.llm.factory import build_models_response
from sentral.llm.ids import ProviderId, TierName


class _TierProviderStub:
    def __init__(self, tiers: list[dict[str, object]]) -> None:
        self._tiers = tiers

    def available_tiers(self) -> list[dict[str, object]]:
        return self._tiers


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
            "gpt-5.6-luna",
            "gpt-5.6-sol",
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
            "claude-sonnet-5",
            "claude-opus-5",
            "claude-fable-5-1",
        ]
