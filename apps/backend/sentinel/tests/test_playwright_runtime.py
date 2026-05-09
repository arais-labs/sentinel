from __future__ import annotations

import json

from app.services.runtime.playwright_runtime import (
    DEFAULT_BROWSER_LOCALE,
    DEFAULT_BROWSER_TIMEZONE_ID,
    DEFAULT_BROWSER_LANGUAGES,
    _build_stealth_init_script,
    build_browser_context_options,
    build_chromium_launch_options,
)


def test_browser_context_options_default_to_native_identity(monkeypatch) -> None:
    for name in ("BROWSER_USER_AGENT", "BROWSER_LOCALE", "BROWSER_TIMEZONE_ID"):
        monkeypatch.delenv(name, raising=False)

    options = build_browser_context_options()

    assert "user_agent" not in options
    assert options["locale"] == DEFAULT_BROWSER_LOCALE
    assert options["timezone_id"] == DEFAULT_BROWSER_TIMEZONE_ID


def test_chromium_launch_options_default_lang(monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_LOCALE", raising=False)
    monkeypatch.delenv("BROWSER_EXTRA_ARGS", raising=False)

    options = build_chromium_launch_options(headless=False)

    assert f"--lang={DEFAULT_BROWSER_LOCALE}" in options["args"]


def test_stealth_script_keeps_native_identity_by_default(monkeypatch) -> None:
    for name in ("BROWSER_USER_AGENT", "BROWSER_LOCALE", "BROWSER_TIMEZONE_ID"):
        monkeypatch.delenv(name, raising=False)

    script = _build_stealth_init_script()

    assert "webdriver" in script
    assert json.dumps(DEFAULT_BROWSER_LANGUAGES) in script
    assert DEFAULT_BROWSER_TIMEZONE_ID in script
    assert "userAgentData" not in script
    assert "MacIntel" not in script
    assert "Google Inc." not in script
