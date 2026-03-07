from __future__ import annotations

from app.services.messages import build_generation_metadata, normalize_generation_metadata


def test_build_generation_metadata_drops_tier_placeholders_for_resolved_fields():
    metadata = build_generation_metadata(
        requested_tier="normal",
        resolved_model="normal",
        provider="tier",
        temperature=0.7,
        max_iterations=50,
    )
    assert metadata.get("requested_tier") == "normal"
    assert "resolved_model" not in metadata
    assert "provider" not in metadata
    assert metadata.get("temperature") == 0.7
    assert metadata.get("max_iterations") == 50


def test_normalize_generation_metadata_reuses_sanitized_contract():
    metadata = normalize_generation_metadata(
        {
            "requested_tier": "hard",
            "resolved_model": "claude-sonnet-4-20250514",
            "provider": "anthropic",
            "temperature": 1,
            "max_iterations": 25,
            "extra": "ignored",
        }
    )
    assert metadata == {
        "requested_tier": "hard",
        "resolved_model": "claude-sonnet-4-20250514",
        "provider": "anthropic",
        "temperature": 1.0,
        "max_iterations": 25,
    }
