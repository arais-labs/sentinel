from __future__ import annotations

import re
from pathlib import Path

THEMED_MARKER = "<!-- sentinel:html -->"
RAW_MARKER = "<!-- sentinel:html-raw -->"

_THEMED_RE = re.compile(r"^\s*<!--\s*sentinel:html\s*-->", re.IGNORECASE)
_RAW_RE = re.compile(r"^\s*<!--\s*sentinel:html-raw\s*-->", re.IGNORECASE)

THEME_CSS = (Path(__file__).parent / "theme.css").read_text(encoding="utf-8")


def post_process_assistant_html(text: str) -> str:
    """Normalize markers and inject Sentinel theme CSS for themed responses.

    Three paths:
    - Raw marker → strip the `-raw` qualifier (normalize to standard marker), no injection.
      The raw qualifier never reaches storage or the frontend.
    - Themed marker → insert `<style>theme.css</style>` immediately after the marker.
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
        return f"{THEMED_MARKER}\n<style>\n{THEME_CSS}\n</style>{rest}"
    return text
