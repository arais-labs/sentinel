from __future__ import annotations

from pydantic import BaseModel, Field


class SkillSummaryResponse(BaseModel):
    name: str
    description: str
    enabled: bool
    builtin: bool
    required_tools: list[str] = Field(default_factory=list)
    required_env: list[str] = Field(default_factory=list)


class SkillDetailResponse(SkillSummaryResponse):
    system_prompt_injection: str


class SkillListResponse(BaseModel):
    items: list[SkillSummaryResponse]


class SkillToggleResponse(BaseModel):
    name: str
    enabled: bool
