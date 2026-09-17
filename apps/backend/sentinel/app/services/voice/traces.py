"""Durable runtime visibility without persisting a Voice chat or raw audio."""

import logging
import json
import time
import traceback
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import update

from app.models import VoiceTrace, VoiceTraceEvent

logger = logging.getLogger(__name__)


def trace_data(value):
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        # Omit known credential fields, provider signatures, and media bytes.
        media = value.get("type") in {"image", "audio"}
        return {
            str(key): (
                "[redacted]"
                if str(key).lower()
                in {
                    "api_key",
                    "authorization",
                    "access_token",
                    "refresh_token",
                    "password",
                    "secret",
                    "signature",
                    "thought_signature",
                    "encrypted_content",
                    "base64",
                    "audio",
                }
                or (media and key == "data")
                else trace_data(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [trace_data(item) for item in value]
    if isinstance(value, (UUID, datetime, Enum)):
        return value.value if isinstance(value, Enum) else str(value)
    if isinstance(value, bytes):
        return {"bytes_omitted": len(value)}
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return json.dumps(trace_data(json.loads(value)), ensure_ascii=False)
        except (ValueError, RecursionError):
            pass
    return value


class TraceRecorder:
    def __init__(self, factory, kind, input):
        self.factory, self.kind, self.input = factory, kind, input
        self.id = uuid4()
        self.started = time.monotonic()
        self.output = None
        self.failure = None
        self.saved = False
        self.pending_event = None
        self.pending_since = time.monotonic()

    def elapsed(self):
        return round((time.monotonic() - self.started) * 1000)

    async def __aenter__(self):
        try:
            async with self.factory() as db:
                db.add(
                    VoiceTrace(
                        id=self.id, kind=self.kind, status="running", input=trace_data(self.input)
                    )
                )
                await db.commit()
            self.saved = True
        except Exception:
            logger.exception("Voice trace storage unavailable")
        return self

    async def record(self, kind, payload):
        if not self.saved:
            return
        try:
            async with self.factory() as db:
                db.add(
                    VoiceTraceEvent(
                        trace_id=self.id,
                        kind=kind,
                        payload=trace_data(payload),
                        elapsed_ms=self.elapsed(),
                    )
                )
                await db.commit()
        except Exception:
            logger.exception("Unable to persist Voice trace event %s", kind)

    async def event(self, event):
        if event.type in {"text_delta", "thinking_delta", "toolcall_delta"}:
            value = trace_data(event)
            if self.pending_event and (
                self.pending_event["type"] != value["type"]
                or self.pending_event.get("metadata") != value.get("metadata")
            ):
                await self.flush_events()
            if self.pending_event:
                self.pending_event["delta"] = (self.pending_event.get("delta") or "") + (
                    value.get("delta") or ""
                )
            else:
                self.pending_event = value
                self.pending_since = time.monotonic()
            # Avoid a database transaction per token, without hiding a long
            # in-progress text/reasoning block from the live trace viewer.
            if (
                len(self.pending_event.get("delta") or "") >= 2048
                or time.monotonic() - self.pending_since >= 0.5
            ):
                await self.flush_events()
            return
        await self.flush_events()
        await self.record(event.type, event)

    async def flush_events(self):
        if self.pending_event:
            event, self.pending_event = self.pending_event, None
            await self.record(event["type"], event)

    async def checkpoint(self, items):
        for item in items:
            await self.record("message", item)

    async def __aexit__(self, exc_type, exc, tb):
        await self.flush_events()
        if exc is not None:
            await self.record(
                "exception",
                {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": "".join(traceback.format_exception(exc_type, exc, tb)),
                },
            )
        if self.saved:
            try:
                async with self.factory() as db:
                    await db.execute(
                        update(VoiceTrace)
                        .where(VoiceTrace.id == self.id)
                        .values(
                            status=(
                                "cancelled"
                                if exc_type and exc_type.__name__ == "CancelledError"
                                else "error" if exc or self.failure else "completed"
                            ),
                            output=trace_data(self.output),
                            error=str(exc) if exc else self.failure,
                            duration_ms=self.elapsed(),
                            finished_at=datetime.now(UTC),
                        )
                    )
                    await db.commit()
            except Exception:
                logger.exception("Unable to finalize Voice trace")
        return False
