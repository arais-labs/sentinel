from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import (
    get_db,
    get_onboarding_service,
    get_runtime_rebuild_service,
)
from app.middleware.auth import TokenPayload, require_auth
from app.services.onboarding.onboarding_service import OnboardingService
from app.services.runtime.runtime_rebuild import RuntimeRebuildService

router = APIRouter()


class CompleteOnboardingRequest(BaseModel):
    agent_name: str | None = None
    agent_role: str | None = None
    agent_personality: str | None = None


@router.get("/status")
async def get_status(
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    onboarding_service: OnboardingService = Depends(get_onboarding_service),
) -> dict[str, bool]:
    return {"completed": await onboarding_service.is_completed(db, user_id=user.sub)}


@router.post("/complete")
async def complete_onboarding(
    payload: CompleteOnboardingRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    onboarding_service: OnboardingService = Depends(get_onboarding_service),
    runtime_rebuild_service: RuntimeRebuildService = Depends(get_runtime_rebuild_service),
) -> dict[str, bool]:
    await onboarding_service.complete(
        db,
        user_id=user.sub,
        agent_name=payload.agent_name,
        agent_role=payload.agent_role,
        agent_personality=payload.agent_personality,
    )
    runtime_rebuild_service.rebuild_agent_runtime_support(request.app.state)
    return {"completed": True}
