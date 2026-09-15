from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import (
    get_db,
    get_onboarding_service,
)
from app.services.onboarding.onboarding_service import OnboardingService

router = APIRouter()


class CompleteOnboardingRequest(BaseModel):
    agent_name: str | None = None
    agent_role: str | None = None
    agent_personality: str | None = None


@router.get("/status")
async def get_status(
    db: AsyncSession = Depends(get_db),
    onboarding_service: OnboardingService = Depends(get_onboarding_service),
) -> dict[str, bool]:
    return {"completed": await onboarding_service.is_completed(db, user_id="local")}


@router.post("/complete")
async def complete_onboarding(
    payload: CompleteOnboardingRequest,
    db: AsyncSession = Depends(get_db),
    onboarding_service: OnboardingService = Depends(get_onboarding_service),
) -> dict[str, bool]:
    await onboarding_service.complete(
        db,
        user_id="local",
        agent_name=payload.agent_name,
        agent_role=payload.agent_role,
        agent_personality=payload.agent_personality,
    )
    return {"completed": True}
