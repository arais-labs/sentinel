from __future__ import annotations

from app.services.agent.interactive_output import (
    RAW_MARKER,
    THEME_CSS,
    THEMED_MARKER,
    post_process_assistant_html,
)


def test_themed_marker_injects_style_block():
    result = post_process_assistant_html(
        f"{THEMED_MARKER}\n<table class=\"sentinel-table\"><tr><td>hi</td></tr></table>"
    )
    assert result.startswith(THEMED_MARKER)
    assert "<style>" in result
    assert "</style>" in result
    assert ".sentinel-table" in result  # token from theme.css present


def test_raw_marker_normalizes_and_skips_injection():
    body = "<div style=\"color:red\">hello</div>"
    result = post_process_assistant_html(f"{RAW_MARKER}\n{body}")
    assert THEMED_MARKER in result
    assert RAW_MARKER not in result
    assert "<style>" not in result
    assert body in result


def test_no_marker_returns_unchanged():
    text = "just a plain text response without any HTML marker"
    assert post_process_assistant_html(text) == text


def test_empty_string_returns_unchanged():
    assert post_process_assistant_html("") == ""


def test_themed_marker_with_leading_whitespace_still_wraps():
    result = post_process_assistant_html(f"   \n{THEMED_MARKER}\n<p>x</p>")
    assert "<style>" in result
    assert ".sentinel-table" in result


def test_raw_marker_case_insensitive():
    body = "<p>hi</p>"
    result = post_process_assistant_html(f"<!-- SENTINEL:HTML-RAW -->\n{body}")
    assert THEMED_MARKER in result
    assert "<style>" not in result
    assert body in result


def test_themed_marker_with_inner_whitespace_still_wraps():
    result = post_process_assistant_html("<!--   sentinel:html   -->\n<p>x</p>")
    assert "<style>" in result


def test_raw_qualifier_never_appears_in_output():
    """Defensive: regardless of input, the persisted output never carries -raw."""
    for input_text in (
        f"{RAW_MARKER}\n<p>hi</p>",
        "<!-- SENTINEL:HTML-RAW -->\n<p>hi</p>",
        f"   {RAW_MARKER}\n<p>hi</p>",
    ):
        assert "html-raw" not in post_process_assistant_html(input_text).lower()


def test_themed_marker_preserves_body_content():
    body = '<div class="sentinel-card"><h1 class="sentinel-heading-1">Hello</h1></div>'
    result = post_process_assistant_html(f"{THEMED_MARKER}\n{body}")
    assert body in result


def test_theme_css_constant_is_loaded():
    assert THEME_CSS  # non-empty
    assert ".sentinel-table" in THEME_CSS
    assert "prefers-color-scheme" in THEME_CSS
