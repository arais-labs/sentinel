from __future__ import annotations

from app.services.agent.interactive_output import (
    POLICY_CONTENT,
    POLICY_EXPLANATION,
    POLICY_TITLE,
    RAW_MARKER,
    THEME_CSS,
    THEMED_MARKER,
)


def test_policy_content_mentions_both_markers():
    assert THEMED_MARKER in POLICY_CONTENT
    assert RAW_MARKER in POLICY_CONTENT


def test_policy_content_references_sentinel_classes():
    assert ".sentinel-table" in POLICY_CONTENT
    assert ".sentinel-button" in POLICY_CONTENT
    assert ".sentinel-badge" in POLICY_CONTENT


def test_policy_metadata_strings_set():
    assert POLICY_TITLE.startswith("Agent Mode Policy")
    assert "Interactive Output" in POLICY_TITLE
    assert POLICY_EXPLANATION.strip()


def test_policy_content_does_not_inline_full_css():
    """CSS body should live in theme.css and be injected at persist time, not in the prompt."""
    assert "--sentinel-bg" not in POLICY_CONTENT
    assert "prefers-color-scheme" not in POLICY_CONTENT


def test_theme_css_loaded_and_has_sentinel_classes():
    assert ".sentinel-table" in THEME_CSS
    assert ".sentinel-button" in THEME_CSS
    assert "--sentinel-bg" in THEME_CSS
    assert "prefers-color-scheme" in THEME_CSS
