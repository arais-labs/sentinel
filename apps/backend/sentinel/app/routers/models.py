from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import get_llm_provider
from app.schemas.models import ModelsResponse
from app.services.llm.generic.base import LLMProvider
from app.services.llm.factory import build_models_response

router = APIRouter()


@router.get("", response_model=ModelsResponse)
async def list_models(
    provider: LLMProvider = Depends(get_llm_provider),
) -> ModelsResponse:
    return build_models_response(provider)
