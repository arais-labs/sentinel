"""Context windows for the supported models, verified against provider docs 2026-09-07."""

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
    "gpt-5.6-sol": 1_050_000,
    "gpt-5.6": 1_050_000,
    "gpt-5.6-terra": 1_050_000,
    "gpt-5.6-luna": 1_050_000,
    "claude-sonnet-5": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-fable-5-1": 1_000_000,
}


def model_context(model, output_reserve=8192):
    window = MODEL_CONTEXT_WINDOWS.get(model)
    reserve = min(max(1, output_reserve), 128_000)
    return {
        "model": model,
        "context_window_tokens": window,
        "output_reserve_tokens": reserve,
        "context_token_budget": max(0, window - reserve) if window else None,
    }
