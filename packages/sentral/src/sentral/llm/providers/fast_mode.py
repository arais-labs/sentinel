"""Shared matching only; each provider owns its supported models and transport."""

import re


def matches_model(model: str, supported: tuple[str, ...]) -> bool:
    """Accept known model IDs and their dated snapshots, not other variants."""

    return any(
        model == name or re.fullmatch(re.escape(name) + r"-(?:\d{8}|\d{4}-\d{2}-\d{2})", model)
        for name in supported
    )
