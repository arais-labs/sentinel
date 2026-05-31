from app.config import Settings


def test_codex_defaults_use_current_codex_slugs():
    settings = Settings(_env_file=None, jwt_secret_key="test-secret")

    assert settings.tier_fast_codex_model == "gpt-5.4-mini"
    assert settings.tier_hard_codex_model == "gpt-5.5"
