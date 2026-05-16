from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).parent
_RULES = (_HERE / "rules.md").read_text(encoding="utf-8").strip()
_COMPONENTS = (_HERE / "components.md").read_text(encoding="utf-8").strip()

POLICY_TITLE = "Agent Mode Policy (Interactive Output)"
POLICY_EXPLANATION = "Mode-specific output capability for this run."

POLICY_CONTENT = (
    "## Agent Mode Policy: Interactive Output\n\n"
    f"{_RULES}\n\n"
    "## Available themed components\n\n"
    f"{_COMPONENTS}"
)
