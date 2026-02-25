from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.middleware.auth import TokenPayload, require_auth
from app.schemas.skills import SkillDetailResponse, SkillListResponse, SkillSummaryResponse, SkillToggleResponse
from app.services.skills.registry import SkillRegistry
from app.services.skills.types import SkillDefinition

router = APIRouter()


@router.get("", response_model=SkillListResponse)
async def list_skills(
    request: Request,
    _: TokenPayload = Depends(require_auth),
) -> SkillListResponse:
    registry = _registry_from_request(request)
    items = [_summary(skill) for skill in registry.list_all()]
    return SkillListResponse(items=items)


@router.get("/{name}", response_model=SkillDetailResponse)
async def get_skill(
    name: str,
    request: Request,
    _: TokenPayload = Depends(require_auth),
) -> SkillDetailResponse:
    registry = _registry_from_request(request)
    skill = registry.get(name)
    if skill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return _detail(skill)


@router.post("/{name}/enable", response_model=SkillToggleResponse)
async def enable_skill(
    name: str,
    request: Request,
    _: TokenPayload = Depends(require_auth),
) -> SkillToggleResponse:
    registry = _registry_from_request(request)
    if not registry.enable(name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return SkillToggleResponse(name=name, enabled=True)


@router.post("/{name}/disable", response_model=SkillToggleResponse)
async def disable_skill(
    name: str,
    request: Request,
    _: TokenPayload = Depends(require_auth),
) -> SkillToggleResponse:
    registry = _registry_from_request(request)
    if not registry.disable(name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    return SkillToggleResponse(name=name, enabled=False)


def _registry_from_request(request: Request) -> SkillRegistry:
    registry = getattr(request.app.state, "skill_registry", None)
    if not isinstance(registry, SkillRegistry):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Skill registry unavailable")
    return registry


def _summary(skill: SkillDefinition) -> SkillSummaryResponse:
    return SkillSummaryResponse(
        name=skill.name,
        description=skill.description,
        enabled=skill.enabled,
        builtin=skill.builtin,
        required_tools=list(skill.required_tools),
        required_env=list(skill.required_env),
    )


def _detail(skill: SkillDefinition) -> SkillDetailResponse:
    return SkillDetailResponse(
        name=skill.name,
        description=skill.description,
        enabled=skill.enabled,
        builtin=skill.builtin,
        required_tools=list(skill.required_tools),
        required_env=list(skill.required_env),
        system_prompt_injection=skill.system_prompt_injection,
    )
