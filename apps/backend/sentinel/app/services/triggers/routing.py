from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Session
from app.services import session_bindings

ROUTE_MODE_MAIN = "main"
ROUTE_MODE_SESSION = "session"
_ROUTE_MODES = {ROUTE_MODE_MAIN, ROUTE_MODE_SESSION}


@dataclass(slots=True)
class AgentMessageRouteResolution:
    session_id: UUID
    route_mode: str
    target_session_id: UUID | None
    used_fallback: bool
    fallback_reason: str | None
    normalized_action_config: dict[str, Any]


def extract_agent_message_target_session_id(
    action_config: dict[str, Any] | None,
) -> UUID | None:
    """Extract intended target session from canonical or legacy fields."""
    if not isinstance(action_config, dict):
        return None
    target = _parse_optional_uuid(action_config.get("target_session_id"))
    if target is not None:
        return target
    return _parse_optional_uuid(action_config.get("session_id"))


async def resolve_agent_message_route(
    db: AsyncSession,
    *,
    user_id: str,
    action_config: dict[str, Any] | None,
) -> AgentMessageRouteResolution:
    """Resolve trigger routing for agent_message actions.

    Behavior:
    - `route_mode=main` always routes to canonical main session.
    - `route_mode=session` routes to `target_session_id` when valid.
    - Invalid/missing target in `session` mode falls back to main.
    - Legacy `session_id` is treated as target input for compatibility.
    - Result always includes canonical routing fields and resolved session id.
    """
    raw = dict(action_config or {})
    route_mode = _normalize_route_mode(raw.get("route_mode"))
    target_session_id = _parse_optional_uuid(raw.get("target_session_id"))
    legacy_session_id = _parse_optional_uuid(raw.get("session_id"))

    # Backward compatibility: if legacy-only session target exists, treat as session route.
    if target_session_id is None and route_mode == ROUTE_MODE_SESSION:
        target_session_id = legacy_session_id
    elif target_session_id is None and route_mode == ROUTE_MODE_MAIN and legacy_session_id is not None:
        route_mode = ROUTE_MODE_SESSION
        target_session_id = legacy_session_id

    main_session = await session_bindings.resolve_or_create_main_session(
        db,
        user_id=user_id,
        agent_id=None,
    )

    resolved = main_session
    used_fallback = False
    fallback_reason: str | None = None

    if route_mode == ROUTE_MODE_SESSION:
        if target_session_id is None:
            used_fallback = True
            fallback_reason = "missing_target_session"
        else:
            candidate = await _get_root_owned_session(
                db,
                user_id=user_id,
                session_id=target_session_id,
            )
            if candidate is None:
                used_fallback = True
                fallback_reason = "invalid_or_deleted_target_session"
            else:
                resolved = candidate

    normalized_route_mode = ROUTE_MODE_MAIN if used_fallback else route_mode
    normalized_target_session_id = None if used_fallback else target_session_id

    normalized_action = dict(raw)
    normalized_action["route_mode"] = normalized_route_mode
    normalized_action["target_session_id"] = (
        str(normalized_target_session_id)
        if normalized_target_session_id is not None
        else None
    )
    normalized_action["resolved_session_id"] = str(resolved.id)
    # Keep legacy key for backward compatibility in schedulers/tooling.
    normalized_action["session_id"] = str(resolved.id)
    if used_fallback and fallback_reason:
        normalized_action["route_fallback_reason"] = fallback_reason
        if target_session_id is not None:
            normalized_action["last_invalid_target_session_id"] = str(target_session_id)
    else:
        normalized_action.pop("route_fallback_reason", None)
        normalized_action.pop("last_invalid_target_session_id", None)

    return AgentMessageRouteResolution(
        session_id=resolved.id,
        route_mode=route_mode,
        target_session_id=target_session_id,
        used_fallback=used_fallback,
        fallback_reason=fallback_reason,
        normalized_action_config=normalized_action,
    )


def _normalize_route_mode(value: Any) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _ROUTE_MODES:
            return normalized
    return ROUTE_MODE_MAIN


def _parse_optional_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return UUID(text)
    except ValueError:
        return None


async def _get_root_owned_session(
    db: AsyncSession,
    *,
    user_id: str,
    session_id: UUID,
) -> Session | None:
    result = await db.execute(
        select(Session).where(
            Session.id == session_id,
            Session.user_id == user_id,
            Session.parent_session_id.is_(None),
        )
    )
    return result.scalars().first()
