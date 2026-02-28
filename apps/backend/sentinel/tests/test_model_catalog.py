from app.services.llm.factory import build_models_response
from app.services.llm.ids import ProviderId, TierName


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
