"""Native module: triggers — scheduled task management."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable
from uuid import UUID

from sqlalchemy import select

from app.database.database import AsyncSessionLocal
from app.models import Session, Trigger
from app.services.tools.executor import ToolValidationError
from app.services.triggers.routing import (
    extract_agent_message_target_session_id,
    resolve_agent_message_route,
)

TriggerHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

ALLOWED_TRIGGER_COMMANDS = ("create", "list", "update", "delete")
_ALLOWED_TYPES = {"cron", "heartbeat"}
_ALLOWED_ACTION_TYPES = {"agent_message", "tool_call", "http_request"}


def _compute_next_fire_at(trigger_type: str, config: dict) -> datetime | None:
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
    db, *, context_session_id: UUID | None, action_config: dict[str, Any],
) -> str:
    target_session_id = extract_agent_message_target_session_id(action_config)
    if context_session_id is None and target_session_id is None:
        raise ToolValidationError(
            "triggers create requires session context. Provide 'session_id' or action_config.target_session_id."
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
            raise ToolValidationError(f"action_config target references unknown session: {target_session_id}")
    if context_user_id and target_user_id and context_user_id != target_user_id:
        raise ToolValidationError("session_id and action_config target belong to different users")
    owner = context_user_id or target_user_id
    if owner is None:
        raise ToolValidationError("Unable to resolve trigger owner user_id from provided session context")
    return owner


async def _resolve_context_user_id(db, payload: dict[str, Any]) -> str:
    context_session_id = _parse_optional_uuid(payload.get("session_id"), field_name="session_id")
    if context_session_id is None:
        raise ToolValidationError("Field 'session_id' is required for scoped trigger access")
    result = await db.execute(select(Session).where(Session.id == context_session_id))
    session = result.scalars().first()
    if session is None:
        raise ToolValidationError(f"session_id references unknown session: {context_session_id}")
    return session.user_id


async def handle_create(payload: dict[str, Any]) -> dict[str, Any]:
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
    if action_type == "agent_message":
        msg = action_config.get("message")
        if not isinstance(msg, str) or not msg.strip():
            raise ToolValidationError("action_config.message must be a non-empty string for agent_message action")
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ToolValidationError("Field 'enabled' must be a boolean")
    next_fire_at = _compute_next_fire_at(trigger_type, config)
    async with AsyncSessionLocal() as db:
        owner_user_id = await _resolve_owner_user_id(
            db, context_session_id=context_session_id, action_config=action_config
        )
        resolved_action_config = action_config
        if action_type == "agent_message":
            route = await resolve_agent_message_route(db, user_id=owner_user_id, action_config=action_config)
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


async def handle_list(payload: dict[str, Any]) -> dict[str, Any]:
    enabled_only = payload.get("enabled_only", False)
    if not isinstance(enabled_only, bool):
        raise ToolValidationError("Field 'enabled_only' must be a boolean")
    async with AsyncSessionLocal() as db:
        owner_user_id = await _resolve_context_user_id(db, payload)
        query = select(Trigger).where(Trigger.user_id == owner_user_id)
        if enabled_only:
            query = query.where(Trigger.enabled.is_(True))
        result = await db.execute(query)
        triggers = result.scalars().all()
        triggers_sorted = sorted(
            triggers,
            key=lambda t: t.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
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


async def handle_update(payload: dict[str, Any]) -> dict[str, Any]:
    trigger_id_raw = payload.get("trigger_id")
    if not isinstance(trigger_id_raw, str) or not trigger_id_raw.strip():
        raise ToolValidationError("Field 'trigger_id' must be a non-empty string UUID")
    try:
        trigger_id = UUID(trigger_id_raw.strip())
    except ValueError as exc:
        raise ToolValidationError(f"Invalid trigger_id UUID: {trigger_id_raw}") from exc
    async with AsyncSessionLocal() as db:
        owner_user_id = await _resolve_context_user_id(db, payload)
        result = await db.execute(
            select(Trigger).where(Trigger.id == trigger_id, Trigger.user_id == owner_user_id)
        )
        trigger = result.scalars().first()
        if trigger is None:
            raise ToolValidationError(f"Trigger {trigger_id} not found")
        changed = False
        if "name" in payload:
            n = payload["name"]
            if not isinstance(n, str) or not n.strip():
                raise ToolValidationError("Field 'name' must be a non-empty string")
            trigger.name = n.strip()
            changed = True
        if "enabled" in payload:
            e = payload["enabled"]
            if not isinstance(e, bool):
                raise ToolValidationError("Field 'enabled' must be a boolean")
            trigger.enabled = e
            if e and trigger.next_fire_at is None:
                trigger.next_fire_at = _compute_next_fire_at(trigger.type, trigger.config or {})
            changed = True
        if "config" in payload:
            c = payload["config"]
            if not isinstance(c, dict):
                raise ToolValidationError("Field 'config' must be an object")
            trigger.config = c
            trigger.next_fire_at = _compute_next_fire_at(trigger.type, c)
            changed = True
        if "action_config" in payload:
            ac = payload["action_config"]
            if not isinstance(ac, dict):
                raise ToolValidationError("Field 'action_config' must be an object")
            if trigger.action_type == "agent_message":
                route = await resolve_agent_message_route(db, user_id=owner_user_id, action_config=ac)
                trigger.action_config = route.normalized_action_config
            else:
                trigger.action_config = ac
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


async def handle_delete(payload: dict[str, Any]) -> dict[str, Any]:
    trigger_id_raw = payload.get("trigger_id")
    if not isinstance(trigger_id_raw, str) or not trigger_id_raw.strip():
        raise ToolValidationError("Field 'trigger_id' must be a non-empty string UUID")
    try:
        trigger_id = UUID(trigger_id_raw.strip())
    except ValueError as exc:
        raise ToolValidationError(f"Invalid trigger_id UUID: {trigger_id_raw}") from exc
    async with AsyncSessionLocal() as db:
        owner_user_id = await _resolve_context_user_id(db, payload)
        result = await db.execute(
            select(Trigger).where(Trigger.id == trigger_id, Trigger.user_id == owner_user_id)
        )
        trigger = result.scalars().first()
        if trigger is None:
            raise ToolValidationError(f"Trigger {trigger_id} not found")
        name = trigger.name
        await db.delete(trigger)
        await db.commit()
    return {"deleted": True, "trigger_id": str(trigger_id), "name": name}


TRIGGER_COMMAND_HANDLERS: dict[str, TriggerHandler] = {
    "create": handle_create,
    "list": handle_list,
    "update": handle_update,
    "delete": handle_delete,
}


async def handle_run(payload: dict[str, Any]) -> dict[str, Any]:
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ToolValidationError("Field 'command' must be a non-empty string")
    normalized = command.strip().lower()
    handler = TRIGGER_COMMAND_HANDLERS.get(normalized)
    if handler is None:
        raise ToolValidationError(
            "Field 'command' must be one of: " + ", ".join(ALLOWED_TRIGGER_COMMANDS)
        )
    return await handler(payload)
