from app.config import Settings


def test_settings_accept_empty_optional_runtime_strings():
    settings = Settings(
        _env_file=None,
        jwt_secret_key="test-secret",
        runtime_qemu_image="",
    )

    assert settings.runtime_qemu_image in (None, "")


def test_settings_accept_empty_optional_runtime_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("RUNTIME_QEMU_IMAGE", "")

    settings = Settings(_env_file=None)

    assert settings.runtime_qemu_image in (None, "")


def test_codex_defaults_use_current_codex_slugs():
    settings = Settings(_env_file=None, jwt_secret_key="test-secret")

    assert settings.tier_fast_codex_model == "gpt-5.4-mini"
    assert settings.tier_hard_codex_model == "gpt-5.5"
