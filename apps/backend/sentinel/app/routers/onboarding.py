from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models.system import SystemSetting

router = APIRouter()


# ── helpers ──────────────────────────────────────────────────────────────────

async def _upsert(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalars().first()
    if setting is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        setting.value = value
    await db.commit()


def _rebuild_llm_provider():
    """Rebuild the LLM provider from current settings using TierProvider."""
    from app.services.llm import AnthropicProvider, CodexProvider, GeminiProvider, OpenAIProvider
    from app.services.llm.tier_provider import TierConfig, TierModelConfig, TierProvider
    from app.services.llm.types import ReasoningConfig

    anthropic = None
    openai = None
    gemini = None
    openai_is_codex = False

    anthropic_token = settings.anthropic_oauth_token or settings.anthropic_api_key
    if anthropic_token:
        anthropic = AnthropicProvider(anthropic_token)

    if settings.openai_oauth_token:
        openai = CodexProvider(settings.openai_oauth_token)
        openai_is_codex = True
    elif settings.openai_api_key:
        openai = OpenAIProvider(settings.openai_api_key, base_url=settings.openai_base_url)

    if settings.gemini_api_key:
        gemini = GeminiProvider(settings.gemini_api_key)

    if not anthropic and not openai and not gemini:
        return None

    tier_defs = [
        ("fast", settings.tier_fast_anthropic_model,
         settings.tier_fast_openai_model, settings.tier_fast_codex_model,
         settings.tier_fast_gemini_model,
         settings.tier_fast_max_tokens, settings.tier_fast_temperature,
         settings.tier_fast_anthropic_thinking_budget, settings.tier_fast_openai_reasoning_effort,
         settings.tier_fast_gemini_thinking_budget),
        ("normal", settings.tier_normal_anthropic_model,
         settings.tier_normal_openai_model, settings.tier_normal_codex_model,
         settings.tier_normal_gemini_model,
         settings.tier_normal_max_tokens, settings.tier_normal_temperature,
         settings.tier_normal_anthropic_thinking_budget, settings.tier_normal_openai_reasoning_effort,
         settings.tier_normal_gemini_thinking_budget),
        ("hard", settings.tier_hard_anthropic_model,
         settings.tier_hard_openai_model, settings.tier_hard_codex_model,
         settings.tier_hard_gemini_model,
         settings.tier_hard_max_tokens, settings.tier_hard_temperature,
         settings.tier_hard_anthropic_thinking_budget, settings.tier_hard_openai_reasoning_effort,
         settings.tier_hard_gemini_thinking_budget),
    ]

    tiers: dict[str, TierConfig] = {}
    for (tier_name, anth_model, oai_model, codex_model, gem_model,
         max_tok, temp, thinking_budget, reasoning_effort, gem_thinking_budget) in tier_defs:
        anth_cfg = None
        oai_cfg = None
        gem_cfg = None
        if anthropic:
            anth_cfg = TierModelConfig(
                provider=anthropic,
                model=anth_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tok,
                    thinking_budget=thinking_budget if thinking_budget > 0 else None,
                ),
                temperature=temp,
            )
        if openai:
            oai_cfg = TierModelConfig(
                provider=openai,
                model=codex_model if openai_is_codex else oai_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tok,
                    reasoning_effort=reasoning_effort or None,
                ),
                temperature=temp,
            )
        if gemini:
            gem_cfg = TierModelConfig(
                provider=gemini,
                model=gem_model,
                reasoning_config=ReasoningConfig(
                    max_tokens=max_tok,
                    thinking_budget=gem_thinking_budget if gem_thinking_budget > 0 else None,
                ),
                temperature=temp,
            )

        all_cfgs: dict[str, TierModelConfig] = {}
        if anth_cfg:
            all_cfgs["anthropic"] = anth_cfg
        if oai_cfg:
            all_cfgs["openai"] = oai_cfg
        if gem_cfg:
            all_cfgs["gemini"] = gem_cfg

        if not all_cfgs:
            continue

        primary_name = settings.primary_provider
        if primary_name in all_cfgs:
            primary = all_cfgs[primary_name]
            fallbacks = [c for name, c in all_cfgs.items() if name != primary_name]
            tiers[tier_name] = TierConfig(primary=primary, fallbacks=fallbacks)
        else:
            cfgs_list = list(all_cfgs.values())
            tiers[tier_name] = TierConfig(primary=cfgs_list[0], fallbacks=cfgs_list[1:])

    return TierProvider(tiers=tiers, default_tier="normal", max_retries=settings.llm_max_retries)


def _rebuild_agent_loop(app_state) -> None:
    """Rebuild the agent loop on app.state using existing registries."""
    from app.services.agent import AgentLoop, ContextBuilder, ToolAdapter

    new_provider = _rebuild_llm_provider()
    if new_provider is None:
        return

    app_state.llm_provider = new_provider

    tool_registry = getattr(app_state, "tool_registry", None)
    tool_executor = getattr(app_state, "tool_executor", None)
    skill_registry = getattr(app_state, "skill_registry", None)
    memory_search_service = getattr(app_state, "memory_search_service", None)

    if tool_registry is None or tool_executor is None:
        return

    available_tools = {tool.name for tool in tool_registry.list_all()}
    context_builder = ContextBuilder(
        default_system_prompt=settings.default_system_prompt,
        skill_registry=skill_registry,
        available_tools=available_tools,
        memory_search_service=memory_search_service,
    )
    tool_adapter = ToolAdapter(tool_registry, tool_executor)
    app_state.agent_loop = AgentLoop(new_provider, context_builder, tool_adapter)

    # Sync Telegram bridge with new agent loop
    bridge = getattr(app_state, "telegram_bridge", None)
    if bridge is not None:
        bridge.update_agent_loop(app_state.agent_loop)


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_status(
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    key = f"onboarding_completed:{user.sub}"
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    return {"completed": result.scalars().first() is not None}


class CompleteOnboardingRequest(BaseModel):
    system_prompt: str | None = None


@router.post("/complete")
async def complete_onboarding(
    payload: CompleteOnboardingRequest = CompleteOnboardingRequest(),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    request: Request = None,
) -> dict:
    await _upsert(db, f"onboarding_completed:{user.sub}", datetime.now(UTC).isoformat())
    if payload.system_prompt and payload.system_prompt.strip():
        settings.default_system_prompt = payload.system_prompt.strip()
        await _upsert(db, "default_system_prompt", payload.system_prompt.strip())
        # Rebuild agent loop so the new prompt takes effect immediately
        if request is not None:
            _rebuild_agent_loop(request.app.state)
    return {"completed": True}


class SetApiKeysRequest(BaseModel):
    anthropic_api_key: str | None = None
    anthropic_oauth_token: str | None = None
    openai_api_key: str | None = None
    openai_oauth_token: str | None = None
    gemini_api_key: str | None = None


@router.post("/api-keys")
async def set_api_keys(
    payload: SetApiKeysRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if payload.anthropic_api_key:
        settings.anthropic_api_key = payload.anthropic_api_key
        await _upsert(db, "anthropic_api_key", payload.anthropic_api_key)
    if payload.anthropic_oauth_token:
        settings.anthropic_oauth_token = payload.anthropic_oauth_token
        await _upsert(db, "anthropic_oauth_token", payload.anthropic_oauth_token)
    if payload.openai_api_key:
        settings.openai_api_key = payload.openai_api_key
        await _upsert(db, "openai_api_key", payload.openai_api_key)
    if payload.openai_oauth_token:
        settings.openai_oauth_token = payload.openai_oauth_token
        await _upsert(db, "openai_oauth_token", payload.openai_oauth_token)
    if payload.gemini_api_key:
        settings.gemini_api_key = payload.gemini_api_key
        await _upsert(db, "gemini_api_key", payload.gemini_api_key)

    # Rebuild agent loop so changes take effect immediately without restart
    _rebuild_agent_loop(request.app.state)

    return {"success": True}


@router.get("/api-keys/status")
async def get_api_keys_status(
    user: TokenPayload = Depends(require_auth),
) -> dict:
    """Return which providers are configured (without exposing actual keys)."""
    def _mask(value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 8:
            return "****"
        return value[:4] + "..." + value[-4:]

    anthropic_key = settings.anthropic_api_key
    anthropic_oauth = settings.anthropic_oauth_token
    openai_key = settings.openai_api_key
    openai_oauth = settings.openai_oauth_token
    gemini_key = settings.gemini_api_key

    return {
        "primary_provider": settings.primary_provider,
        "providers": {
            "anthropic": {
                "configured": bool(anthropic_key or anthropic_oauth),
                "auth_method": "oauth" if anthropic_oauth else ("api_key" if anthropic_key else None),
                "masked_key": _mask(anthropic_oauth or anthropic_key),
            },
            "openai": {
                "configured": bool(openai_key or openai_oauth),
                "auth_method": "oauth" if openai_oauth else ("api_key" if openai_key else None),
                "masked_key": _mask(openai_oauth or openai_key),
            },
            "gemini": {
                "configured": bool(gemini_key),
                "auth_method": "api_key" if gemini_key else None,
                "masked_key": _mask(gemini_key),
            },
        },
    }


class DeleteProviderRequest(BaseModel):
    provider: str  # "anthropic", "openai", or "gemini"


async def _delete_setting(db: AsyncSession, key: str) -> None:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalars().first()
    if setting is not None:
        await db.delete(setting)
        await db.commit()


@router.delete("/api-keys")
async def delete_api_keys(
    payload: DeleteProviderRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Remove all keys for a provider."""
    if payload.provider == "anthropic":
        settings.anthropic_api_key = None
        settings.anthropic_oauth_token = None
        await _delete_setting(db, "anthropic_api_key")
        await _delete_setting(db, "anthropic_oauth_token")
    elif payload.provider == "openai":
        settings.openai_api_key = None
        settings.openai_oauth_token = None
        await _delete_setting(db, "openai_api_key")
        await _delete_setting(db, "openai_oauth_token")
    elif payload.provider == "gemini":
        settings.gemini_api_key = None
        await _delete_setting(db, "gemini_api_key")
    else:
        return {"success": False, "error": "Unknown provider"}

    _rebuild_agent_loop(request.app.state)
    return {"success": True}


class SetPrimaryProviderRequest(BaseModel):
    provider: str  # "anthropic", "openai", or "gemini"


@router.post("/primary-provider")
async def set_primary_provider(
    payload: SetPrimaryProviderRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Set which provider is primary (the other becomes fallback)."""
    if payload.provider not in ("anthropic", "openai", "gemini"):
        return {"success": False, "error": "Must be 'anthropic', 'openai', or 'gemini'"}

    settings.primary_provider = payload.provider
    await _upsert(db, "primary_provider", payload.provider)
    _rebuild_agent_loop(request.app.state)
    return {"success": True, "primary_provider": payload.provider}
