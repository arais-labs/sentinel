import pytest
from textual.widgets import Input, Select
from sentinel_tui.setup import Setup
from sentral.llm.tier_defaults import TierDefaults


@pytest.mark.asyncio
async def test_setup_uses_shared_defaults_and_requires_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = Setup()
    async with app.run_test() as pilot:
        await pilot.click("#start")
        assert app.return_value is None
        app.query_one("#key", Input).value = "test-key"
        await pilot.pause()
        await pilot.click("#start")
    assert app.return_value.model == TierDefaults().tier_normal_openai_model
    assert app.return_value.generation.provider_metadata["reasoning_effort"] == "medium"
    assert app.return_value.api_key == "test-key"


@pytest.mark.asyncio
async def test_provider_change_uses_matching_environment_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    app = Setup()
    async with app.run_test() as pilot:
        app.query_one("#provider", Select).value = "anthropic"
        await pilot.pause()
        assert app.query_one("#url", Input).disabled
        await pilot.click("#start")
    assert app.return_value.provider == "anthropic"
    assert app.return_value.api_key == "test-anthropic-key"
    assert app.return_value.base_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
async def test_oauth_import_is_explicit_and_uses_selected_provider(monkeypatch, provider):
    from sentinel_tui import setup

    calls = []

    async def imported(selected):
        calls.append(selected)
        return "test-oauth-credential"

    monkeypatch.setattr(setup, "import_login", imported)
    app = Setup(provider=provider)
    async with app.run_test() as pilot:
        assert calls == []
        app.query_one("#auth", Select).value = "oauth"
        await pilot.pause()
        assert not app.query_one("#key", Input).display
        await pilot.click("#start")
    assert calls == [provider]
    assert app.return_value.auth_method == "oauth"
    assert app.return_value.api_key == "test-oauth-credential"
    assert "test-oauth-credential" not in repr(app.return_value)


@pytest.mark.asyncio
async def test_failed_oauth_import_can_retry(monkeypatch):
    from sentinel_tui import setup
    from textual.widgets import Button

    async def missing(_):
        raise ValueError("Sign in with Codex CLI, then try again.")

    monkeypatch.setattr(setup, "import_login", missing)
    app = Setup()
    async with app.run_test() as pilot:
        app.query_one("#auth", Select).value = "oauth"
        await pilot.pause()
        await pilot.click("#start")
        await pilot.pause()
        assert not app.query_one("#start", Button).disabled
        assert app.return_value is None
