from app.config import Settings


def test_openai_defaults_use_current_models_for_both_auth_methods():
    settings = Settings(_env_file=None)

    for provider in ("openai", "codex"):
        assert getattr(settings, f"tier_fast_{provider}_model") == "gpt-5.6-luna"
        assert getattr(settings, f"tier_normal_{provider}_model") == "gpt-5.6-sol"
        assert getattr(settings, f"tier_hard_{provider}_model") == "gpt-6-astra"
    assert settings.tier_fast_openai_reasoning_effort == "low"
    assert settings.tier_normal_openai_reasoning_effort == "medium"
    assert settings.tier_hard_openai_reasoning_effort == "high"


def test_explicit_model_choices_are_preserved():
    settings = Settings(
        _env_file=None,
        tier_normal_openai_model="gpt-5.6-terra",
        tier_normal_codex_model="gpt-5.6-terra",
    )

    assert settings.tier_normal_openai_model == "gpt-5.6-terra"
    assert settings.tier_normal_codex_model == "gpt-5.6-terra"


def test_current_claude_defaults():
    settings = Settings(_env_file=None)
    assert settings.default_model == "claude-opus-5"
    assert settings.tier_fast_anthropic_model == "claude-sonnet-5"
    assert settings.tier_normal_anthropic_model == "claude-opus-5"
    assert settings.tier_hard_anthropic_model == "claude-fable-5-1"
