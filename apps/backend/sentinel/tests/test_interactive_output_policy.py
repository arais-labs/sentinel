from __future__ import annotations

from app.services.agent.policies import INTERACTIVE_OUTPUT_POLICY
from app.services.agent.interactive_output import (
    RAW_MARKER,
    THEME_CSS,
    THEMED_MARKER,
)


def test_policy_content_mentions_both_markers():
    assert THEMED_MARKER in INTERACTIVE_OUTPUT_POLICY.content
    assert RAW_MARKER in INTERACTIVE_OUTPUT_POLICY.content


def test_policy_content_references_sentinel_classes():
    assert ".sentinel-table" in INTERACTIVE_OUTPUT_POLICY.content
    assert ".sentinel-button" in INTERACTIVE_OUTPUT_POLICY.content
    assert ".sentinel-badge" in INTERACTIVE_OUTPUT_POLICY.content


def test_policy_metadata_strings_set():
    assert INTERACTIVE_OUTPUT_POLICY.title.startswith("Agent Mode Policy")
    assert "Interactive Output" in INTERACTIVE_OUTPUT_POLICY.title
    assert INTERACTIVE_OUTPUT_POLICY.explanation.strip()


def test_policy_content_does_not_inline_full_css():
    """CSS body should live in theme.css and be injected at persist time, not in the prompt."""
    assert "--sentinel-bg" not in INTERACTIVE_OUTPUT_POLICY.content
    assert "prefers-color-scheme" not in INTERACTIVE_OUTPUT_POLICY.content


def test_theme_css_loaded_and_has_sentinel_classes():
    assert ".sentinel-table" in THEME_CSS
    assert ".sentinel-button" in THEME_CSS
    assert "--sentinel-bg" in THEME_CSS
    assert "prefers-color-scheme" in THEME_CSS
