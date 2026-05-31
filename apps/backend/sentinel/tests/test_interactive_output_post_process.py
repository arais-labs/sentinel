from __future__ import annotations

from app.services.agent.interactive_output import (
    RAW_MARKER,
    THEME_CSS,
    THEMED_MARKER,
    post_process_assistant_html,
)


def test_themed_marker_injects_style_block():
    result = post_process_assistant_html(
        f'{THEMED_MARKER}\n<table class="sentinel-table"><tr><td>hi</td></tr></table>'
    )
    assert result.startswith(THEMED_MARKER)
    assert "<style>" in result
    assert "</style>" in result
    assert ".sentinel-table" in result  # token from theme.css present


def test_raw_marker_normalizes_and_skips_injection():
    body = '<div style="color:red">hello</div>'
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


def test_themed_marker_strips_agent_pasted_theme_copy():
    """Agents sometimes copy the auto-injected <style> block from earlier turns.
    The duplicate would override the live theme. post_process must strip it."""
    agent_copy = (
        "<style>\n"
        "/* Sentinel themed components for HTML artifacts.\n"
        "   Auto-injected when the assistant uses the <!-- sentinel:html --> marker. */\n"
        "body { padding: 99px; background: red; }\n"
        "</style>"
    )
    body = '<div class="sentinel-card"><p>hello</p></div>'
    text = f"{THEMED_MARKER}\n{agent_copy}\n{body}"
    result = post_process_assistant_html(text)
    # The agent's copy must be gone — no padding:99px, no red background
    assert "padding: 99px" not in result
    assert "background: red" not in result
    # The canonical theme CSS must be present
    assert ".sentinel-table" in result  # from theme.css
    # The body content must survive
    assert body in result


def test_themed_marker_keeps_small_override_style_blocks():
    """Custom overrides without the auto-injection signature must survive."""
    override = "<style>.my-custom { color: red; }</style>"
    body = '<div class="my-custom sentinel-card">hi</div>'
    text = f"{THEMED_MARKER}\n{override}\n{body}"
    result = post_process_assistant_html(text)
    assert override in result
    assert body in result
    # Theme is still injected
    assert ".sentinel-table" in result


def test_theme_css_constant_is_loaded():
    assert THEME_CSS  # non-empty
    assert ".sentinel-table" in THEME_CSS
    assert "prefers-color-scheme" in THEME_CSS
