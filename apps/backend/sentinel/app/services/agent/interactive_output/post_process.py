from __future__ import annotations

import re
from pathlib import Path

THEMED_MARKER = "<!-- sentinel:html -->"
RAW_MARKER = "<!-- sentinel:html-raw -->"

_THEMED_RE = re.compile(r"^\s*<!--\s*sentinel:html\s*-->", re.IGNORECASE)
_RAW_RE = re.compile(r"^\s*<!--\s*sentinel:html-raw\s*-->", re.IGNORECASE)

THEME_CSS = (Path(__file__).parent / "theme.css").read_text(encoding="utf-8")

# Distinctive comment present in our theme.css header. If the agent copies the
# auto-injected <style> block from earlier turns into its own response (LLMs
# often mimic previous assistant outputs), the duplicate would override our
# canonical injection via CSS cascade order. We strip any <style> block
# carrying this signature before re-injecting the canonical theme.
_AUTO_INJECTION_SIGNATURE = "Auto-injected when the assistant uses the"
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.DOTALL | re.IGNORECASE)


def _strip_auto_injection_copies(content: str) -> str:
    def _drop_if_copy(match: re.Match[str]) -> str:
        return "" if _AUTO_INJECTION_SIGNATURE in match.group() else match.group()

    return _STYLE_BLOCK_RE.sub(_drop_if_copy, content)


def post_process_assistant_html(text: str) -> str:
    """Normalize markers and inject Sentinel theme CSS for themed responses.

    Three paths:
    - Raw marker → strip the `-raw` qualifier (normalize to standard marker), no injection.
      The raw qualifier never reaches storage or the frontend.
    - Themed marker → strip any duplicate auto-injection copies the agent may have
      pasted into its response, then insert the canonical `<style>theme.css</style>`
      immediately after the marker.
    - No recognized marker → return unchanged.
    """
    if not text:
        return text
    raw_match = _RAW_RE.match(text)
    if raw_match:
        rest = text[raw_match.end() :]
        return f"{THEMED_MARKER}{rest}"
    themed_match = _THEMED_RE.match(text)
    if themed_match:
        rest = text[themed_match.end() :]
        rest = _strip_auto_injection_copies(rest)
        return f"{THEMED_MARKER}\n<style>\n{THEME_CSS}\n</style>{rest}"
    return text
