"""Request-local routing and conservative cross-provider reasoning controls."""

from dataclasses import replace
from typing import Literal

from sentral.llm.ids import ProviderId, TierName

ReasoningLevel = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def selection_model(tier=None, provider_id=None, reasoning_level=None, fast_mode=False):
    tier = TierName(tier or TierName.NORMAL).value
    if provider_id is None and reasoning_level is None and not fast_mode:
        return tier
    provider = ProviderId(provider_id).value if provider_id is not None else "auto"
    if reasoning_level is not None and reasoning_level not in LEVELS:
        raise ValueError("Unknown reasoning level")
    return f'sentinel:{tier}:{provider}:{reasoning_level or "default"}' + (
        ":fast" if fast_mode else ""
    )


def reasoning_kind(model):
    if model == "gemini-pro-agent":
        return "effort"
    if (
        model.startswith(
            (
                "gemini-3-flash",
                "gemini-3.1-pro",
                "gemini-3.1-flash-lite",
                "gemini-3.8-flash",
                "gemini-3.5-flash-lite",
            )
        )
        and "image" not in model
    ):
        return "effort"
    if model.startswith(("gpt-5", "gpt-6", "o3", "o4")):
        return "effort"
    if model.startswith(
        (
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-fable-5",
            "claude-mythos-5",
            "claude-opus-4-6",
            "claude-sonnet-4-6",
            "claude-opus-4-7",
            "claude-opus-4-8",
        )
    ):
        return "effort"
    if model.startswith(("claude-3-7", "claude-sonnet-4", "claude-opus-4", "gemini-2.5")):
        return "budget"
    return None


def reasoning_levels(model):
    """Provider effort values, rather than a three-position UI normalization.

    Sources: OpenAI model pages; Claude effort; Gemini thinking documentation.
    Unknown families retain the conservative controls supported previously.
    """
    base = ["low", "medium", "high"]
    if model.startswith(
        (
            "gpt-6-astra",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-fable-5",
            "claude-mythos-5",
            "claude-opus-4-7",
            "claude-opus-4-8",
        )
    ):
        return [*base, "xhigh", "max"]
    if model.startswith("gpt-5.6"):
        return ["none", *base, "xhigh", "max"]
    if model.startswith("claude-opus-4-6"):
        return [*base, "max"]
    if model.startswith(("gpt-5.2", "gpt-5.3", "gpt-5.4", "gpt-5.5")):
        return (["none"] if "codex" not in model else []) + base + ["xhigh"]
    if model.startswith("gpt-5.1"):
        return (
            (["none"] if "codex" not in model else [])
            + base
            + (["xhigh"] if "codex-max" in model else [])
        )
    if model.startswith("gpt-5") and "codex" not in model:
        return ["minimal", *base]
    if model.startswith(("gemini-3-flash", "gemini-3.1-flash-lite")):
        return ["minimal", *base]
    return base if reasoning_kind(model) else []


def with_reasoning(config, level):
    kind = reasoning_kind(config.model)
    if kind is None:
        raise ValueError("This model does not expose a supported reasoning control")
    if level not in reasoning_levels(config.model):
        raise ValueError(f"Reasoning level {level} is not supported by {config.model}")
    rc = config.reasoning_config
    if kind == "effort":
        rc = replace(rc, reasoning_effort=level, thinking_budget=None)
    else:
        budget = {"low": 1024, "medium": 4096, "high": 16384}[level]
        rc = replace(
            rc,
            thinking_budget=budget,
            reasoning_effort=None,
            max_tokens=max(rc.max_tokens, budget + 4096),
        )
    return replace(config, reasoning_config=rc)
