from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Session, Trigger, TriggerLog
from app.services.agent import AgentLoop
from app.services.triggers.routing import (
    extract_agent_message_target_session_id,
    resolve_agent_message_route,
)
from app.services.tools import ToolExecutor
from app.services.ws_manager import ConnectionManager

logger = logging.getLogger(__name__)


class TriggerOwnershipError(ValueError):
    """Raised when a trigger cannot be safely mapped to a real user owner."""


def compute_next_fire_at(
    trigger_type: str,
    config: dict | None,
    *,
    reference_time: datetime | None = None,
) -> datetime | None:
    now = _as_utc(reference_time or datetime.now(UTC))
    payload = config if isinstance(config, dict) else {}

    if trigger_type == "cron":
        expr = payload.get("expr") or payload.get("cron")
        if not isinstance(expr, str) or not expr.strip():
            raise ValueError("Cron trigger requires config field 'expr' or 'cron'")
        try:
            next_run = croniter(expr.strip(), now).get_next(datetime)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Invalid cron expression: {expr}") from exc
        return _as_utc(next_run)

    if trigger_type == "heartbeat":
        interval = payload.get("interval_seconds", payload.get("interval"))
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
            raise ValueError("Heartbeat trigger requires positive config field 'interval_seconds'")
        return now + timedelta(seconds=int(interval))

    return None


class TriggerScheduler:
    def __init__(
        self,
        *,
        agent_loop: AgentLoop | None,
        tool_executor: ToolExecutor | None,
        ws_manager: ConnectionManager | None = None,
        db_factory: async_sessionmaker[AsyncSession] | None,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._agent_loop = agent_loop
        self._tool_executor = tool_executor
        self._ws_manager = ws_manager
        self._db_factory = db_factory
        self._poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        self._in_flight: set[str] = set()

    def set_agent_loop(self, agent_loop: AgentLoop | None) -> None:
        """Hot-swap the agent loop used for agent_message actions."""
        self._agent_loop = agent_loop

    async def fire_now(
        self,
        db: AsyncSession,
        *,
        trigger_id: UUID,
        input_payload: dict | None = None,
        force: bool = True,
    ) -> TriggerLog | None:
        """Execute a trigger immediately using the provided DB session."""
        return await self._fire_trigger_with_db(
            db,
            trigger_id,
            input_payload=input_payload,
            force=force,
        )

    async def start(self, stop_event: asyncio.Event) -> None:
        if self._db_factory is None:
            return

        while not stop_event.is_set():
            try:
                await self._poll_once()
            except Exception as exc:  # noqa: BLE001
                logger.warning("trigger scheduler poll failed: %s", exc)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval_seconds)
            except TimeoutError:
                continue

        await self._drain_in_flight()

    async def _poll_once(self) -> None:
        if self._db_factory is None:
            return
        now = datetime.now(UTC)
        async with self._db_factory() as db:
            due = await self._due_triggers(db, now)

        for trigger in due:
            key = str(trigger.id)
            if key in self._in_flight:
                continue
            self._in_flight.add(key)
            asyncio.create_task(self._fire_wrapper(trigger.id, key))

    async def _fire_wrapper(self, trigger_id: UUID, key: str) -> None:
        try:
            await self._fire_trigger(trigger_id)
        finally:
            self._in_flight.discard(key)

    async def _drain_in_flight(self, *, timeout_seconds: float = 2.0) -> None:
        if not self._in_flight:
            return
        deadline = asyncio.get_running_loop().time() + max(0.1, timeout_seconds)
        while self._in_flight and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)

    async def _due_triggers(self, db: AsyncSession, now: datetime) -> list[Trigger]:
        result = await db.execute(select(Trigger))
        rows = result.scalars().all()
        due: list[Trigger] = []
        for trigger in rows:
            if not trigger.enabled:
                continue
            if trigger.type not in {"cron", "heartbeat"}:
                continue
            if trigger.next_fire_at is None:
                continue
            if _as_utc(trigger.next_fire_at) <= now:
                due.append(trigger)
        due.sort(key=lambda item: item.next_fire_at or now)
        return due

    async def _fire_trigger(self, trigger_id: UUID) -> None:
        if self._db_factory is None:
            return

        async with self._db_factory() as db:
            await self._fire_trigger_with_db(db, trigger_id)

    async def _fire_trigger_with_db(
        self,
        db: AsyncSession,
        trigger_id: UUID,
        *,
        input_payload: dict | None = None,
        force: bool = False,
    ) -> TriggerLog | None:
        fired_at = datetime.now(UTC)
        started = time.perf_counter()
        trigger = await self._load_trigger(db, trigger_id)
        if trigger is None:
            return None
        if not trigger.enabled and not force:
            return None

        log_entry: TriggerLog
        try:
            output_summary = await self._execute_action(db, trigger)
            duration_ms = max(0, int((time.perf_counter() - started) * 1000))

            trigger.last_fired_at = fired_at
            trigger.fire_count = int(trigger.fire_count or 0) + 1
            trigger.consecutive_errors = 0
            trigger.last_error = None
            if trigger.enabled:
                trigger.next_fire_at = self._next_fire_after_success(trigger, fired_at)

            log_entry = TriggerLog(
                trigger_id=trigger.id,
                fired_at=fired_at,
                status="fired",
                duration_ms=duration_ms,
                input_payload=input_payload,
                output_summary=output_summary,
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = max(0, int((time.perf_counter() - started) * 1000))
            message = str(exc)
            ownership_error = isinstance(exc, TriggerOwnershipError)

            trigger.error_count = int(trigger.error_count or 0) + 1
            trigger.consecutive_errors = int(trigger.consecutive_errors or 0) + 1
            trigger.last_error = message
            if ownership_error:
                trigger.enabled = False
                trigger.next_fire_at = None
            elif trigger.consecutive_errors >= 5:
                trigger.enabled = False
                trigger.next_fire_at = None
            elif trigger.enabled:
                trigger.next_fire_at = self._next_fire_after_failure(trigger, fired_at)

            log_entry = TriggerLog(
                trigger_id=trigger.id,
                fired_at=fired_at,
                status="failed",
                duration_ms=duration_ms,
                input_payload=input_payload,
                error_message=message[:1000],
            )

        db.add(log_entry)
        await db.commit()
        return log_entry

    async def _load_trigger(self, db: AsyncSession, trigger_id: UUID) -> Trigger | None:
        result = await db.execute(select(Trigger).where(Trigger.id == trigger_id))
        return result.scalars().first()

    def _next_fire_after_success(self, trigger: Trigger, fired_at: datetime) -> datetime | None:
        next_fire = compute_next_fire_at(trigger.type, trigger.config, reference_time=fired_at)
        if next_fire is None:
            trigger.enabled = False
        return next_fire

    def _next_fire_after_failure(self, trigger: Trigger, fired_at: datetime) -> datetime | None:
        try:
            return compute_next_fire_at(trigger.type, trigger.config, reference_time=fired_at)
        except Exception:
            return None

    async def _execute_action(self, db: AsyncSession, trigger: Trigger) -> str:
        if trigger.action_type == "agent_message":
            return await self._execute_agent_message(db, trigger)
        if trigger.action_type == "tool_call":
            return await self._execute_tool_call(trigger)
        if trigger.action_type == "http_request":
            return await self._execute_http_request(trigger)
        raise ValueError(f"Unsupported action_type: {trigger.action_type}")

    async def _execute_agent_message(self, db: AsyncSession, trigger: Trigger) -> str:
        if self._agent_loop is None:
            raise RuntimeError("Agent loop unavailable")
        action = trigger.action_config if isinstance(trigger.action_config, dict) else {}
        message = action.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("agent_message action requires non-empty 'message'")
        effective_user_id = await self._resolve_effective_user_id(db, trigger, action)
        route = await resolve_agent_message_route(
            db,
            user_id=effective_user_id,
            action_config=action,
        )
        trigger.action_config = route.normalized_action_config
        session_id = route.session_id
        session_key = str(session_id)
        if route.used_fallback:
            logger.info(
                "Trigger %s route fallback applied (%s), resolved to main session %s",
                trigger.id,
                route.fallback_reason or "unknown",
                session_id,
            )

        async def _on_event(event: Any) -> None:
            if self._ws_manager:
                await self._ws_manager.broadcast_agent_event(session_key, event)

        if self._ws_manager:
            await self._ws_manager.broadcast_message_ack(
                session_key,
                message_id=f"trig-{trigger.id}-{int(time.time())}",
                content=f"[Trigger: {trigger.name}] {message.strip()}",
                created_at=datetime.now(UTC)
            )
            await self._ws_manager.broadcast_agent_thinking(session_key)

        result = await self._agent_loop.run(
            db, 
            session_id, 
            message.strip(), 
            stream=True, # Enable streaming for real-time UI updates
            on_event=_on_event,
            allow_high_risk=True,
        )
        return f"agent_message:{result.final_text[:500]}"

    async def _resolve_effective_user_id(
        self,
        db: AsyncSession,
        trigger: Trigger,
        action: dict,
    ) -> str:
        user_id = (trigger.user_id or "").strip()
        if user_id:
            return user_id

        candidate_session_id = extract_agent_message_target_session_id(action)
        if candidate_session_id is not None:
            result = await db.execute(
                select(Session).where(Session.id == candidate_session_id)
            )
            session = result.scalars().first()
            if session is not None:
                trigger.user_id = session.user_id
                return session.user_id
            raise TriggerOwnershipError(
                f"Trigger {trigger.id} references missing action session: {candidate_session_id}"
            )

        raise TriggerOwnershipError(
            f"Trigger {trigger.id} has no owner user_id and no action_config target session"
        )

    async def _execute_tool_call(self, trigger: Trigger) -> str:
        if self._tool_executor is None:
            raise RuntimeError("Tool executor unavailable")
        action = trigger.action_config if isinstance(trigger.action_config, dict) else {}
        
        # New model: 'name' and 'arguments'
        # Old model fallback: 'tool_name' and 'payload'
        tool_name = action.get("name") or action.get("tool_name") or action.get("tool")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("tool_call action requires non-empty 'name'")
            
        payload = action.get("arguments") or action.get("payload") or action.get("input", {})
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ValueError("tool_call action arguments must be an object")

        result, _ = await self._tool_executor.execute(tool_name.strip(), payload, allow_high_risk=True)
        return f"tool_call:{tool_name.strip()}:{_truncate_json(result)}"

    async def _execute_http_request(self, trigger: Trigger) -> str:
        action = trigger.action_config if isinstance(trigger.action_config, dict) else {}
        url = action.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("http_request action requires non-empty 'url'")
        method = action.get("method", "POST")
        if not isinstance(method, str):
            raise ValueError("http_request action 'method' must be a string")
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("http_request action has unsupported method")

        headers = action.get("headers", {})
        if headers is None:
            headers = {}
        if not isinstance(headers, dict):
            raise ValueError("http_request action 'headers' must be an object")

        timeout_seconds = action.get("timeout_seconds", 10)
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("http_request action 'timeout_seconds' must be positive")

        request_kwargs: dict = {"headers": {str(k): str(v) for k, v in headers.items()}}
        if "body" in action:
            body = action["body"]
            if isinstance(body, (dict, list)):
                request_kwargs["json"] = body
            else:
                request_kwargs["content"] = str(body)

        async with httpx.AsyncClient(timeout=float(timeout_seconds)) as client:
            response = await client.request(method, url.strip(), **request_kwargs)
        return f"http_request:{method} {url.strip()} => {response.status_code}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _truncate_json(value: object) -> str:
    try:
        rendered = json.dumps(value, default=str)
    except TypeError:
        rendered = str(value)
    if len(rendered) <= 500:
        return rendered
    return rendered[:500] + "..."
