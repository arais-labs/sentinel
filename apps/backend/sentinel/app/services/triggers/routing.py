from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Session


class AgentMessageRouteError(ValueError):
    """Raised when an agent_message trigger cannot resolve an explicit target session."""


@dataclass(slots=True)
class AgentMessageRouteResolution:
    session_id: UUID
    target_session_id: UUID | None
    normalized_action_config: dict[str, Any]


def extract_agent_message_target_session_id(
    action_config: dict[str, Any] | None,
) -> UUID | None:
    """Extract intended target session from the canonical target field."""
    if not isinstance(action_config, dict):
        return None
    return _parse_optional_uuid(action_config.get("target_session_id"))


async def resolve_agent_message_route(
    db: AsyncSession,
    *,
    user_id: str,
    action_config: dict[str, Any] | None,
) -> AgentMessageRouteResolution:
    """Resolve trigger routing for agent_message actions.

    Agent-message triggers must name one existing root session owned by the trigger user.
    Legacy `main` routing and missing targets are invalid and do not fall back.
    """
    raw = dict(action_config or {})
    target_session_id = _parse_optional_uuid(raw.get("target_session_id"))
    raw_route_mode = raw.get("route_mode")
    if isinstance(raw_route_mode, str) and raw_route_mode.strip().lower() == "main":
        raise AgentMessageRouteError(
            "agent_message triggers must target a specific session; choose a session"
        )
    if target_session_id is None:
        raise AgentMessageRouteError(
            "agent_message triggers require action_config.target_session_id"
        )

    resolved = await _get_root_owned_session(
        db,
        user_id=user_id,
        session_id=target_session_id,
    )
    if resolved is None:
        raise AgentMessageRouteError(f"target session is missing or deleted: {target_session_id}")

    normalized_action = dict(raw)
    normalized_action.pop("route_mode", None)
    normalized_action["target_session_id"] = str(target_session_id)
    normalized_action["resolved_session_id"] = str(resolved.id)
    normalized_action.pop("session_id", None)
    normalized_action.pop("route_fallback_reason", None)
    normalized_action.pop("last_invalid_target_session_id", None)

    return AgentMessageRouteResolution(
        session_id=resolved.id,
        target_session_id=target_session_id,
        normalized_action_config=normalized_action,
    )


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
