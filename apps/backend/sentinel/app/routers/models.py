from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_request_instance_runtime_context
from app.schemas.models import ModelsResponse
from app.services.instance_runtime_context import InstanceRuntimeContext
from app.services.llm.factory import build_models_response

router = APIRouter()


@router.get("", response_model=ModelsResponse)
async def list_models(
    _db: AsyncSession = Depends(get_db),
    context: InstanceRuntimeContext = Depends(get_request_instance_runtime_context),
) -> ModelsResponse:
    provider = (
        context.agent_runtime_support.provider
        if context.agent_runtime_support is not None
        else None
    )
    return build_models_response(provider)
