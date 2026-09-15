from __future__ import annotations

from fastapi import APIRouter

from app.schemas.agent_modes import AgentModeOptionResponse, AgentModesResponse
from app.services.agent.agent_modes import (
    get_default_agent_mode,
    list_agent_mode_definitions,
)

router = APIRouter()


@router.get("", response_model=AgentModesResponse)
async def list_agent_modes() -> AgentModesResponse:
    modes = list_agent_mode_definitions()
    return AgentModesResponse(
        items=[
            AgentModeOptionResponse(
                id=item.id,
                label=item.label,
                description=item.description,
            )
            for item in modes
        ],
        default_mode=get_default_agent_mode(),
    )
