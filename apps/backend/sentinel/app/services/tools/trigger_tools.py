from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Session, Trigger
from app.services.triggers.routing import (
    extract_agent_message_target_session_id,
    resolve_agent_message_route,
)
from app.services.tools.executor import ToolValidationError
from app.services.tools.registry import ToolDefinition

_ALLOWED_TYPES = {"cron", "heartbeat"}
_ALLOWED_ACTION_TYPES = {"agent_message", "tool_call", "http_request"}


def _compute_next_fire_at(trigger_type: str, config: dict) -> datetime | None:
    """Inline subset of TriggerScheduler.compute_next_fire_at to avoid circular imports."""
    from datetime import timedelta

    now = datetime.now(UTC)
    if trigger_type == "cron":
        from croniter import croniter
        expr = config.get("expr") or config.get("cron")
        if not isinstance(expr, str) or not expr.strip():
            raise ToolValidationError("Cron config requires field 'expr' with a valid cron expression")
        try:
            return croniter(expr.strip(), now).get_next(datetime).replace(tzinfo=UTC)
        except Exception as exc:
            raise ToolValidationError(f"Invalid cron expression '{expr}': {exc}") from exc
    if trigger_type == "heartbeat":
        interval = config.get("interval_seconds", config.get("interval"))
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
            raise ToolValidationError("Heartbeat config requires positive 'interval_seconds'")
        return now + timedelta(seconds=int(interval))
    return None


def _parse_optional_uuid(value: Any, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolValidationError(f"Field '{field_name}' must be a valid non-empty UUID string")
    try:
        return UUID(value.strip())
    except ValueError as exc:
        raise ToolValidationError(f"Field '{field_name}' must be a valid UUID") from exc


async def _resolve_owner_user_id(
    db: AsyncSession,
    *,
    context_session_id: UUID | None,
    action_config: dict[str, Any],
) -> str:
    target_session_id = extract_agent_message_target_session_id(action_config)
    if context_session_id is None and target_session_id is None:
        raise ToolValidationError(
            "trigger_create requires session context to avoid dangling triggers. "
            "Provide 'session_id' or action_config.target_session_id."
        )

    context_user_id: str | None = None
    target_user_id: str | None = None

    if context_session_id is not None:
        result = await db.execute(select(Session).where(Session.id == context_session_id))
        session = result.scalars().first()
        if session is None:
            raise ToolValidationError(f"session_id references unknown session: {context_session_id}")
        context_user_id = session.user_id

    if target_session_id is not None:
        result = await db.execute(select(Session).where(Session.id == target_session_id))
        session = result.scalars().first()
        if session is not None:
            target_user_id = session.user_id
        elif context_user_id is None:
            # Without context session, unknown target leaves owner unresolved.
            raise ToolValidationError(
                f"action_config target references unknown session: {target_session_id}"
            )

    if context_user_id and target_user_id and context_user_id != target_user_id:
        raise ToolValidationError(
            "session_id and action_config target belong to different users"
        )

    owner = context_user_id or target_user_id
    if owner is None:
        raise ToolValidationError("Unable to resolve trigger owner user_id from provided session context")
    return owner


async def _resolve_context_user_id(db: AsyncSession, payload: dict[str, Any]) -> str:
    context_session_id = _parse_optional_uuid(payload.get("session_id"), field_name="session_id")
    if context_session_id is None:
        raise ToolValidationError("Field 'session_id' is required for scoped trigger access")
    result = await db.execute(select(Session).where(Session.id == context_session_id))
    session = result.scalars().first()
    if session is None:
        raise ToolValidationError(f"session_id references unknown session: {context_session_id}")
    return session.user_id


def trigger_create_tool(session_factory: async_sessionmaker[AsyncSession]) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolValidationError("Field 'name' must be a non-empty string")

        trigger_type = payload.get("type")
        if trigger_type not in _ALLOWED_TYPES:
            raise ToolValidationError(f"Field 'type' must be one of: {sorted(_ALLOWED_TYPES)}")

        config = payload.get("config", {})
        if not isinstance(config, dict):
            raise ToolValidationError("Field 'config' must be an object")

        action_type = payload.get("action_type")
        if action_type not in _ALLOWED_ACTION_TYPES:
            raise ToolValidationError(f"Field 'action_type' must be one of: {sorted(_ALLOWED_ACTION_TYPES)}")

        action_config = payload.get("action_config", {})
        if not isinstance(action_config, dict):
            raise ToolValidationError("Field 'action_config' must be an object")

        context_session_id = _parse_optional_uuid(payload.get("session_id"), field_name="session_id")

        # Validate action-specific requirements
        if action_type == "agent_message":
            msg = action_config.get("message")
            if not isinstance(msg, str) or not msg.strip():
                raise ToolValidationError("action_config.message must be a non-empty string for agent_message action")

        enabled = payload.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ToolValidationError("Field 'enabled' must be a boolean")

        next_fire_at = _compute_next_fire_at(trigger_type, config)

        async with session_factory() as db:
            owner_user_id = await _resolve_owner_user_id(
                db,
                context_session_id=context_session_id,
                action_config=action_config,
            )
            resolved_action_config = action_config
            if action_type == "agent_message":
                route = await resolve_agent_message_route(
                    db,
                    user_id=owner_user_id,
                    action_config=action_config,
                )
                resolved_action_config = route.normalized_action_config
            trigger = Trigger(
                user_id=owner_user_id,
                name=name.strip(),
                type=trigger_type,
                config=config,
                action_type=action_type,
                action_config=resolved_action_config,
                enabled=enabled,
                next_fire_at=next_fire_at,
            )
            db.add(trigger)
            await db.commit()
            await db.refresh(trigger)

            return {
                "trigger_id": str(trigger.id),
                "name": trigger.name,
                "type": trigger.type,
                "action_type": trigger.action_type,
                "enabled": trigger.enabled,
                "next_fire_at": trigger.next_fire_at.isoformat() if trigger.next_fire_at else None,
                "created_at": trigger.created_at.isoformat() if trigger.created_at else None,
            }

    return ToolDefinition(
        name="trigger_create",
        description=(
            "Create a scheduled trigger that automatically runs an agent message or tool call on a schedule. "
            "Use 'cron' type with config.expr (standard cron expression) for calendar-based schedules, "
            "or 'heartbeat' type with config.interval_seconds for fixed intervals. "
            "For agent_message action, set action_config.message and optional routing fields: "
            "action_config.route_mode ('main' or 'session') and action_config.target_session_id. "
            "Invalid session routes auto-fallback to main. "
            "Returns trigger_id — store it if you need to update or delete later."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "type", "config", "action_type", "action_config"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable trigger name, e.g. 'Daily standup at 9am'",
                },
                "type": {
                    "type": "string",
                    "enum": ["cron", "heartbeat"],
                    "description": "'cron' for calendar-based (requires config.expr), 'heartbeat' for fixed interval (requires config.interval_seconds)",
                },
                "config": {
                    "type": "object",
                    "description": "For cron: {\"expr\": \"0 9 * * MON-FRI\"}. For heartbeat: {\"interval_seconds\": 3600}",
                },
                "action_type": {
                    "type": "string",
                    "enum": ["agent_message", "tool_call", "http_request"],
                    "description": "What to do when trigger fires. 'agent_message' is the most common — sends a message to the agent in a session.",
                },
                "action_config": {
                    "type": "object",
                    "description": (
                        "For agent_message: {\"message\": \"...\", \"route_mode\": \"main\"} "
                        "or {\"message\": \"...\", \"route_mode\": \"session\", \"target_session_id\": \"<session UUID>\"}. "
                        "For tool_call: {\"name\": \"tool_name\", \"arguments\": {...}}. "
                        "For http_request: {\"url\": \"...\", \"method\": \"POST\", \"headers\": {}, \"body\": null}."
                    ),
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Owning Sentinel session UUID used for safe user binding. "
                        "Auto-injected when called from an active session."
                    ),
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Whether the trigger is active immediately. Defaults to true.",
                },
            },
        },
        execute=_execute,
    )


def trigger_list_tool(session_factory: async_sessionmaker[AsyncSession]) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        enabled_only = payload.get("enabled_only", False)

        async with session_factory() as db:
            owner_user_id = await _resolve_context_user_id(db, payload)
            query = select(Trigger).where(Trigger.user_id == owner_user_id)
            if enabled_only:
                query = query.where(Trigger.enabled.is_(True))
            result = await db.execute(query)
            triggers = result.scalars().all()
            triggers_sorted = sorted(triggers, key=lambda t: t.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)

            return {
                "triggers": [
                    {
                        "trigger_id": str(t.id),
                        "name": t.name,
                        "type": t.type,
                        "enabled": t.enabled,
                        "action_type": t.action_type,
                        "action_config": t.action_config,
                        "next_fire_at": t.next_fire_at.isoformat() if t.next_fire_at else None,
                        "last_fired_at": t.last_fired_at.isoformat() if t.last_fired_at else None,
                        "fire_count": t.fire_count,
                        "consecutive_errors": t.consecutive_errors,
                        "last_error": t.last_error,
                    }
                    for t in triggers_sorted
                ],
                "total": len(triggers_sorted),
            }

    return ToolDefinition(
        name="trigger_list",
        description="List all scheduled triggers with their current status, next fire time, and action config.",
        risk_level="low",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["session_id"],
            "properties": {
                "enabled_only": {
                    "type": "boolean",
                    "description": "If true, only return enabled (active) triggers. Defaults to false.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Current session UUID for owner-scoped trigger visibility.",
                },
            },
        },
        execute=_execute,
    )


def trigger_update_tool(session_factory: async_sessionmaker[AsyncSession]) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        trigger_id_raw = payload.get("trigger_id")
        if not isinstance(trigger_id_raw, str) or not trigger_id_raw.strip():
            raise ToolValidationError("Field 'trigger_id' must be a non-empty string UUID")
        try:
            trigger_id = UUID(trigger_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError(f"Invalid trigger_id UUID: {trigger_id_raw}") from exc

        async with session_factory() as db:
            owner_user_id = await _resolve_context_user_id(db, payload)
            result = await db.execute(
                select(Trigger).where(
                    Trigger.id == trigger_id,
                    Trigger.user_id == owner_user_id,
                )
            )
            trigger = result.scalars().first()
            if trigger is None:
                raise ToolValidationError(f"Trigger {trigger_id} not found")

            changed = False

            if "name" in payload:
                name = payload["name"]
                if not isinstance(name, str) or not name.strip():
                    raise ToolValidationError("Field 'name' must be a non-empty string")
                trigger.name = name.strip()
                changed = True

            if "enabled" in payload:
                enabled = payload["enabled"]
                if not isinstance(enabled, bool):
                    raise ToolValidationError("Field 'enabled' must be a boolean")
                trigger.enabled = enabled
                if enabled and trigger.next_fire_at is None:
                    # Re-arm: compute next fire time from current config
                    trigger.next_fire_at = _compute_next_fire_at(trigger.type, trigger.config or {})
                changed = True

            if "config" in payload:
                config = payload["config"]
                if not isinstance(config, dict):
                    raise ToolValidationError("Field 'config' must be an object")
                trigger.config = config
                trigger.next_fire_at = _compute_next_fire_at(trigger.type, config)
                changed = True

            if "action_config" in payload:
                action_config = payload["action_config"]
                if not isinstance(action_config, dict):
                    raise ToolValidationError("Field 'action_config' must be an object")
                if trigger.action_type == "agent_message":
                    route = await resolve_agent_message_route(
                        db,
                        user_id=owner_user_id,
                        action_config=action_config,
                    )
                    trigger.action_config = route.normalized_action_config
                else:
                    trigger.action_config = action_config
                changed = True

            if changed:
                await db.commit()
                await db.refresh(trigger)

            return {
                "trigger_id": str(trigger.id),
                "name": trigger.name,
                "enabled": trigger.enabled,
                "next_fire_at": trigger.next_fire_at.isoformat() if trigger.next_fire_at else None,
                "updated": changed,
            }

    return ToolDefinition(
        name="trigger_update",
        description=(
            "Update an existing trigger. Use to enable/disable, rename, change schedule (config), "
            "or change what happens when it fires (action_config). Only provide fields you want to change."
        ),
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["trigger_id", "session_id"],
            "properties": {
                "trigger_id": {
                    "type": "string",
                    "description": "UUID of the trigger to update",
                },
                "session_id": {
                    "type": "string",
                    "description": "Current session UUID used for owner-scoped trigger updates.",
                },
                "name": {
                    "type": "string",
                    "description": "New name for the trigger",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Enable or disable the trigger",
                },
                "config": {
                    "type": "object",
                    "description": "New schedule config. For cron: {\"expr\": \"...\"}, for heartbeat: {\"interval_seconds\": N}",
                },
                "action_config": {
                    "type": "object",
                    "description": "New action config, e.g. updated message or session_id",
                },
            },
        },
        execute=_execute,
    )


def trigger_delete_tool(session_factory: async_sessionmaker[AsyncSession]) -> ToolDefinition:
    async def _execute(payload: dict[str, Any]) -> dict[str, Any]:
        trigger_id_raw = payload.get("trigger_id")
        if not isinstance(trigger_id_raw, str) or not trigger_id_raw.strip():
            raise ToolValidationError("Field 'trigger_id' must be a non-empty string UUID")
        try:
            trigger_id = UUID(trigger_id_raw.strip())
        except ValueError as exc:
            raise ToolValidationError(f"Invalid trigger_id UUID: {trigger_id_raw}") from exc

        async with session_factory() as db:
            owner_user_id = await _resolve_context_user_id(db, payload)
            result = await db.execute(
                select(Trigger).where(
                    Trigger.id == trigger_id,
                    Trigger.user_id == owner_user_id,
                )
            )
            trigger = result.scalars().first()
            if trigger is None:
                raise ToolValidationError(f"Trigger {trigger_id} not found")

            name = trigger.name
            await db.delete(trigger)
            await db.commit()

        return {"deleted": True, "trigger_id": str(trigger_id), "name": name}

    return ToolDefinition(
        name="trigger_delete",
        description="Permanently delete a scheduled trigger by its ID. Use trigger_list to find the trigger_id first.",
        risk_level="medium",
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["trigger_id", "session_id"],
            "properties": {
                "trigger_id": {
                    "type": "string",
                    "description": "UUID of the trigger to delete",
                },
                "session_id": {
                    "type": "string",
                    "description": "Current session UUID used for owner-scoped trigger deletion.",
                },
            },
        },
        execute=_execute,
    )
