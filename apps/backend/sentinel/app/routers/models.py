from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/")
async def list_models(request: Request):
    provider = getattr(request.app.state, "llm_provider", None)

    # Dynamic response from TierProvider when available
    if provider is not None and hasattr(provider, "available_tiers"):
        tier_models = provider.available_tiers()
        # Add backward-compat hidden aliases
        visible_ids = {m["id"] for m in tier_models}
        aliases = [
            {"id": "hint:reasoning", "label": "Reasoning", "description": "Alias for Normal tier",
             "tier": "normal", "hidden": True},
            {"id": "hint:anthropic", "label": "Claude", "description": "Alias for Normal tier",
             "tier": "normal", "hidden": True},
        ]
        for alias in aliases:
            if alias["id"] not in visible_ids:
                tier_models.append(alias)
        return {"models": tier_models, "default": "hint:normal"}

    # Fallback when no provider is configured
    return {
        "models": [
            {"id": "hint:fast", "label": "Fast", "description": "Quick responses", "tier": "fast"},
            {"id": "hint:normal", "label": "Normal", "description": "Balanced quality and speed", "tier": "normal"},
            {"id": "hint:hard", "label": "Deep Think", "description": "Extended reasoning", "tier": "hard"},
        ],
        "default": "hint:normal",
    }
