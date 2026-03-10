from __future__ import annotations

from pydantic import BaseModel

from app.services.agent.agent_modes import AgentMode


class AgentModeOptionResponse(BaseModel):
    id: AgentMode
    label: str
    description: str


class AgentModesResponse(BaseModel):
    items: list[AgentModeOptionResponse]
    default_mode: AgentMode
