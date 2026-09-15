from __future__ import annotations

from pydantic import BaseModel, Field

from sentral.llm.ids import ProviderId, TierName


class ModelFallbackResponse(BaseModel):
    provider_id: ProviderId
    model: str


class ModelOptionResponse(BaseModel):
    provider_options: list[dict] = Field(default_factory=list)
    label: str
    description: str
    tier: TierName
    primary_provider_id: ProviderId | None = None
    primary_model_id: str | None = None
    fallback_providers: list[ModelFallbackResponse] = Field(default_factory=list)
    thinking_budget: int | None = None
    reasoning_effort: str | None = None
    context_window_tokens: int | None = None
    context_token_budget: int | None = None
    output_reserve_tokens: int | None = None


class ModelsResponse(BaseModel):
    models: list[ModelOptionResponse]
    default_tier: TierName | None = None
