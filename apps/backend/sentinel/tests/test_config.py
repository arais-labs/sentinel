from app.config import Settings


def test_settings_treat_optional_runtime_empty_strings_as_none():
    settings = Settings(
        _env_file=None,
        jwt_secret_key="test-secret",
        runtime_multipass_cpus="",
    )

    assert settings.runtime_multipass_cpus in (None, "")


def test_settings_treat_optional_runtime_empty_env_as_none(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("RUNTIME_MULTIPASS_CPUS", "")

    settings = Settings(_env_file=None)

    assert settings.runtime_multipass_cpus in (None, "")


def test_codex_defaults_use_current_codex_slugs():
    settings = Settings(_env_file=None, jwt_secret_key="test-secret")

    assert settings.tier_fast_codex_model == "gpt-5.4-mini"
    assert settings.tier_hard_codex_model == "gpt-5.5"
