from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.dependencies import (
    get_db,
    get_request_db_factory,
    get_request_instance_runtime_context,
    get_request_run_registry,
)
from app.models import VoiceTrace, VoiceTraceEvent
from app.services.modules.builtins.session_layout.module import voice_ui_key
from app.services.runtime.ssh_runtime import invalidate_runtime_for_session
from app.services.voice import VoiceGateway, MAX_AUDIO_BYTES, VoiceUnavailable
from app.services.voice.runtime import VoiceRuntime
from app.services.voice.session import reset_voice_session, voice_session
from app.services.voice.traces import TraceRecorder

router = APIRouter()


class VoiceSpeech(BaseModel):
    parent_trace_id: UUID | None = None
    text: str = Field(min_length=1, max_length=400)
    lease_id: UUID
    speed: float = Field(default=1.05, ge=0.75, le=2.0, strict=True)


@router.get("/session")
async def voice_session_info(db: AsyncSession = Depends(get_db)) -> dict:
    """The instance's Voice conversation; created on first use, never listed as a chat."""
    session = await voice_session(db)
    return {"session_id": str(session.id)}


@router.post("/session/reset")
async def voice_session_reset(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Discard the Voice conversation and start a fresh one."""
    previous = await voice_session(db)
    await get_request_run_registry(request).cancel_and_wait(str(previous.id))
    if previous.workspace_id:
        await invalidate_runtime_for_session(request.path_params["instance_name"], previous.id)
    session = await reset_voice_session(db)
    return {"session_id": str(session.id), "previous_session_id": str(previous.id)}


@router.websocket("/ui")
async def voice_ui(websocket: WebSocket, db: AsyncSession = Depends(get_db)) -> None:
    """Window-scoped layout acknowledgements for Voice's workspace actions."""
    manager = websocket.app.state.ws_manager
    key = voice_ui_key(websocket.state.instance_name)
    await db.commit()
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            if isinstance(payload, dict):
                manager.layout_message(key, websocket, payload)
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        await manager.disconnect(key, websocket)


@router.post("/speak")
async def voice_speak(
    payload: VoiceSpeech, request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    try:
        async with TraceRecorder(
            get_request_db_factory(request), "speech", payload.model_dump(mode="json")
        ) as trace:
            result = await request.app.state.voice_runtime.synthesize(
                payload.text, str(payload.lease_id), speed=payload.speed
            )
            trace.output = {
                "mime_type": result.get("mime_type"),
                "audio_generated": True,
                "engine": "Kokoro",
            }
            return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except (TimeoutError, BrokenPipeError, KeyError) as exc:
        raise HTTPException(503, "Local speech playback is unavailable. Reconnect Voice.") from exc


@router.get("/status")
async def voice_status(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    return VoiceGateway(_provider(request), request.app.state.voice_runtime).status()


def _provider(request):
    support = get_request_instance_runtime_context(request).agent_runtime_support
    return support.provider if support else None


@router.post("/runtime/install")
async def voice_install(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    return await request.app.state.voice_runtime.install()


@router.post("/runtime/connect")
async def voice_connect(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        return {"lease_id": await request.app.state.voice_runtime.acquire()}
    except VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/runtime/leases/{lease_id}")
async def voice_heartbeat(
    lease_id: UUID, request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    try:
        request.app.state.voice_runtime.renew(str(lease_id))
        return {"ok": True}
    except VoiceUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/runtime/leases/{lease_id}")
async def voice_disconnect(
    lease_id: UUID, request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    await request.app.state.voice_runtime.release(str(lease_id))
    return {"ok": True}


@router.delete("/runtime")
async def voice_remove(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    runtime: VoiceRuntime = request.app.state.voice_runtime
    await runtime.remove()
    return runtime.status()


@router.post("/transcribe")
async def voice_transcribe(
    request: Request, lease_id: UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    audio = bytearray()
    async for chunk in request.stream():
        audio.extend(chunk)
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Voice recordings are limited to 30 seconds.")
    try:
        async with TraceRecorder(
            get_request_db_factory(request),
            "transcription",
            {
                "lease_id": str(lease_id),
                "audio_bytes": len(audio),
                "engine": "Whisper",
                "language": "English",
            },
        ) as trace:
            text = await VoiceGateway(runtime=request.app.state.voice_runtime).transcribe(
                bytes(audio), str(lease_id)
            )
            trace.output = {"text": text}
            return {"text": text, "trace_id": str(trace.id) if trace.saved else None}
    except VoiceUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (httpx.HTTPError, TimeoutError, BrokenPipeError, KeyError) as exc:
        raise HTTPException(
            503, "Local speech recognition is unavailable. Reconnect Voice."
        ) from exc


def _trace_payload(row):
    return {
        "id": str(row.id),
        "kind": row.kind,
        "status": row.status,
        "input": row.input,
        "output": row.output,
        "error": row.error,
        "duration_ms": row.duration_ms,
        "created_at": row.created_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


@router.get("/traces")
async def voice_traces(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    kind: Literal["all", "turns", "audio"] = "turns",
    db: AsyncSession = Depends(get_db),
):
    rows = (
        (
            await db.execute(
                select(VoiceTrace)
                .where(
                    VoiceTrace.kind.in_(
                        ["turn", "report"]
                        if kind == "turns"
                        else (
                            ["speech", "transcription"]
                            if kind == "audio"
                            else ["turn", "report", "speech", "transcription"]
                        )
                    )
                )
                .order_by(VoiceTrace.created_at.desc(), VoiceTrace.id.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    items = []
    for row in rows[:limit]:
        item = _trace_payload(row)
        source = row.input or {}
        output = row.output or {}
        item["preview"] = str(
            source.get("text") or output.get("text") or output.get("reply") or row.kind
        )[:200]
        item["selection"] = {
            key: source.get(key) for key in ("provider_id", "tier", "reasoning_level", "fast_mode")
        }
        item.pop("input")
        item.pop("output")
        items.append(item)
    return {"items": items, "next_offset": offset + limit if len(rows) > limit else None}


@router.get("/traces/{trace_id}")
async def voice_trace_detail(
    trace_id: UUID, after: int = Query(0, ge=0), db: AsyncSession = Depends(get_db)
):
    row = await db.get(VoiceTrace, trace_id)
    if row is None:
        raise HTTPException(404, "Voice trace not found.")
    events = (
        (
            await db.execute(
                select(VoiceTraceEvent)
                .where(VoiceTraceEvent.trace_id == trace_id, VoiceTraceEvent.id > after)
                .order_by(VoiceTraceEvent.id)
                .limit(101)
            )
        )
        .scalars()
        .all()
    )
    return {
        **_trace_payload(row),
        "related": [
            {"id": str(item.id), "kind": item.kind, "status": item.status}
            for item in (
                await db.execute(
                    select(VoiceTrace)
                    .where(VoiceTrace.input["parent_trace_id"].as_string() == str(trace_id))
                    .order_by(VoiceTrace.created_at)
                )
            )
            .scalars()
            .all()
        ],
        "events": [
            {
                "id": item.id,
                "kind": item.kind,
                "payload": item.payload,
                "elapsed_ms": item.elapsed_ms,
                "created_at": item.created_at.isoformat(),
            }
            for item in events[:100]
        ],
        "next_after": events[99].id if len(events) > 100 else None,
    }


class VoicePlaybackEvent(BaseModel):
    status: Literal["started", "completed", "interrupted", "failed", "skipped"]
    message: str | None = Field(default=None, max_length=500)


@router.post("/traces/{trace_id}/playback")
async def voice_playback_event(
    trace_id: UUID, payload: VoicePlaybackEvent, db: AsyncSession = Depends(get_db)
):
    row = await db.get(VoiceTrace, trace_id)
    if row is None or row.kind not in {"turn", "report"}:
        raise HTTPException(404, "Voice turn trace not found.")
    db.add(
        VoiceTraceEvent(
            trace_id=trace_id,
            kind="playback",
            payload={"source": "client", **payload.model_dump()},
            elapsed_ms=max(0, round((datetime.now(UTC) - row.created_at).total_seconds() * 1000)),
        )
    )
    await db.commit()
    return {"ok": True}
