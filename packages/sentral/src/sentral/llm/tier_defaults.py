"""Provider model and effort defaults shared by Sentinel applications."""

from pydantic import BaseModel

from sentral import GenerationConfig
from sentral.llm.ids import TierName

TIER_LABELS = {
    TierName.FAST: ("Fast", "Quick responses, minimal reasoning"),
    TierName.NORMAL: ("Normal", "Balanced quality and speed"),
    TierName.HARD: ("Deep Think", "Extended reasoning for complex problems"),
}


class TierDefaults(BaseModel):
    # --- Tier: Fast ---
    tier_fast_anthropic_model: str = "claude-sonnet-5"
    tier_fast_openai_model: str = "gpt-5.6-luna"
    tier_fast_codex_model: str = "gpt-5.6-luna"
    tier_fast_gemini_model: str = "gemini-3.5-flash-lite"
    tier_fast_max_tokens: int = 4096
    tier_fast_temperature: float = 0.3
    tier_fast_anthropic_reasoning_effort: str = "low"
    tier_fast_openai_reasoning_effort: str = "low"
    tier_fast_gemini_thinking_budget: int = 0

    # --- Tier: Normal ---
    tier_normal_anthropic_model: str = "claude-opus-5"
    tier_normal_openai_model: str = "gpt-5.6-sol"
    tier_normal_codex_model: str = "gpt-5.6-sol"
    tier_normal_gemini_model: str = "gemini-3.8-flash"
    tier_normal_max_tokens: int = 8192
    tier_normal_temperature: float = 0.7
    tier_normal_anthropic_reasoning_effort: str = "medium"
    tier_normal_openai_reasoning_effort: str = "medium"
    tier_normal_gemini_thinking_budget: int = 0

    # --- Tier: Hard ---
    tier_hard_anthropic_model: str = "claude-fable-5-1"
    tier_hard_openai_model: str = "gpt-6-astra"
    tier_hard_codex_model: str = "gpt-6-astra"
    tier_hard_gemini_model: str = "gemini-3.1-pro-preview"
    tier_hard_max_tokens: int = 40000
    tier_hard_temperature: float = 0.7
    tier_hard_anthropic_reasoning_effort: str = "high"
    tier_hard_openai_reasoning_effort: str = "high"
    tier_hard_gemini_thinking_budget: int = 32000

    def generation(self, provider: str, tier: str) -> GenerationConfig:
        tier = TierName(tier).value
        model = getattr(self, f"tier_{tier}_{provider}_model")
        metadata = {}
        if provider == "gemini":
            budget = getattr(self, f"tier_{tier}_gemini_thinking_budget")
            if budget > 0:
                metadata["thinking_budget"] = budget
        else:
            reasoning_provider = "openai" if provider == "codex" else provider
            metadata["reasoning_effort"] = getattr(
                self, f"tier_{tier}_{reasoning_provider}_reasoning_effort"
            )
        return GenerationConfig(
            model=model,
            temperature=getattr(self, f"tier_{tier}_temperature"),
            max_output_tokens=getattr(self, f"tier_{tier}_max_tokens"),
            provider_metadata=metadata,
        )
