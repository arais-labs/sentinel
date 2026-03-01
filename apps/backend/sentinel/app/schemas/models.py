from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.llm.ids import ProviderId, TierName


class ModelFallbackResponse(BaseModel):
    provider_id: ProviderId
    model: str


class ModelOptionResponse(BaseModel):
    label: str
    description: str
    tier: TierName
    primary_provider_id: ProviderId | None = None
    primary_model_id: str | None = None
    fallback_providers: list[ModelFallbackResponse] = Field(default_factory=list)
    thinking_budget: int | None = None
    reasoning_effort: str | None = None


class ModelsResponse(BaseModel):
    models: list[ModelOptionResponse]
    default_tier: TierName | None = None
