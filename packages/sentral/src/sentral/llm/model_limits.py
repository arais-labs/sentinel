"""Context windows and output caps for supported models; current defaults verified 2026-09-30."""

MODEL_CONTEXT_WINDOWS = {
    "gemini-pro-agent": 1_048_576,
    "gemini-3.8-flash-tiered": 1_048_576,
    "gemini-3.6-flash-medium": 1_048_576,
    "gemini-3.1-pro-high": 1_048_576,
    "gemini-3.1-pro-low": 1_048_576,
    "gemini-3.8-flash": 1_048_576,
    "gemini-3.5-flash-lite": 1_048_576,
    "gemini-3.1-flash-lite": 1_048_576,
    "gemini-3-flash-preview": 1_048_576,
    "gemini-3.1-pro-preview": 1_048_576,
    "gpt-6-astra": 1_050_000,
    "gpt-6.1-sol": 1_050_000,
    "gpt-6-luna": 1_050_000,
    "gpt-5.6-sol": 1_050_000,
    "gpt-5.6": 1_050_000,
    "gpt-5.6-terra": 1_050_000,
    "gpt-5.6-luna": 1_050_000,
    "claude-sonnet-5": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-sonnet-5-5": 1_000_000,
    "claude-opus-5-5": 1_000_000,
    "claude-fable-5-1": 1_000_000,
}

# The largest reply each model may write, as the provider itself reports it.
# Measured 2026-10-24: a streamed request with max_tokens=10_000_000 and a one-word
# prompt, refused before generation with
#   "max_tokens: 10000000 > 128000, which is the maximum allowed number of output
#    tokens for <model>"
# (same text for every Claude model below; streaming needs no extra header at 128000).
# Not listed, so on DEFAULT_MAX_OUTPUT_TOKENS: Gemini through Code Assist accepts
# maxOutputTokens=10_000_000 without naming a maximum; GPT models were not measurable
# (no API key, Codex login expired; the Codex endpoint takes no output cap at all).
MODEL_MAX_OUTPUT_TOKENS = {
    "claude-sonnet-5": 128_000,
    "claude-opus-5": 128_000,
    "claude-sonnet-5-5": 128_000,
    "claude-opus-5-5": 128_000,
    "claude-fable-5-1": 128_000,
}
DEFAULT_MAX_OUTPUT_TOKENS = 32_000


def max_output_tokens(model):
    """The model's output cap; unknown models get a default bounded by their context."""
    if model in MODEL_MAX_OUTPUT_TOKENS:
        return MODEL_MAX_OUTPUT_TOKENS[model]
    window = MODEL_CONTEXT_WINDOWS.get(model)
    return min(DEFAULT_MAX_OUTPUT_TOKENS, window) if window else DEFAULT_MAX_OUTPUT_TOKENS


def output_cap(model, requested=None):
    """What one call may write: the model's cap, or a smaller explicit request."""
    cap = max_output_tokens(model)
    return min(max(1, requested), cap) if requested else cap


def model_context(model, output_reserve=None):
    window = MODEL_CONTEXT_WINDOWS.get(model)
    reserve = min(output_cap(model, output_reserve), 128_000)
    return {
        "model": model,
        "context_window_tokens": window,
        "output_reserve_tokens": reserve,
        "context_token_budget": max(0, window - reserve) if window else None,
    }
