from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.models import Memory, Message, Session
from app.services.agent_run_registry import AgentRunRegistry

logger = logging.getLogger(__name__)
from app.schemas.sessions import (
    ChatResponse,
    ChatRequest,
    CreateMessageRequest,
    CreateSessionRequest,
    MessageListResponse,
    MessageResponse,
    SessionListResponse,
    SessionResponse,
)

router = APIRouter()


@router.get("")
async def list_sessions(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    result = await db.execute(
        select(Session).where(
            Session.user_id == user.sub,
            Session.parent_session_id.is_(None),
        )
    )
    sessions = result.scalars().all()
    if status_filter:
        sessions = [s for s in sessions if s.status == status_filter]
    sessions.sort(key=lambda s: s.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    paged = sessions[offset : offset + limit]
    return SessionListResponse(
        items=[_session_response(item) for item in paged],
        total=len(sessions),
    )


@router.post("")
async def create_session(
    payload: CreateSessionRequest,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    session = Session(
        user_id=user.sub,
        agent_id=user.agent_id,
        title=payload.title,
        status="active",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _session_response(session)


@router.get("/default")
async def get_default_session(
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """Return the user's primary session, creating one if none exists."""
    result = await db.execute(
        select(Session).where(
            Session.user_id == user.sub,
            Session.status == "active",
            Session.parent_session_id.is_(None),
        )
    )
    sessions = result.scalars().all()
    if sessions:
        sessions.sort(key=lambda s: s.created_at or datetime.min.replace(tzinfo=UTC))
        return _session_response(sessions[0])

    session = Session(user_id=user.sub, agent_id=user.agent_id, title="Main", status="active")
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return _session_response(session)


@router.post("/default/reset")
async def reset_default_session(
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """End the current primary session and start a fresh one. Memory is preserved."""
    result = await db.execute(
        select(Session).where(
            Session.user_id == user.sub,
            Session.status == "active",
            Session.parent_session_id.is_(None),
        )
    )
    old_sessions = result.scalars().all()
    old_session_ids = [s.id for s in old_sessions]

    for old_session in old_sessions:
        old_session.status = "ended"
        old_session.ended_at = datetime.now(UTC)

    session = Session(user_id=user.sub, agent_id=user.agent_id, title="Main", status="active")
    db.add(session)
    await db.commit()
    await db.refresh(session)

    # Fire-and-forget: extract memories from ended sessions
    provider = getattr(request.app.state, "agent_loop", None)
    db_factory = getattr(request.app.state, "db_factory", None)
    if provider is not None and old_session_ids:
        asyncio.create_task(
            _extract_session_memories(old_session_ids, user.sub, request)
        )

    return _session_response(session)


async def _extract_session_memories(
    session_ids: list[UUID],
    user_id: str,
    request: Request,
) -> None:
    """Summarise ended sessions into Memory nodes (fire-and-forget)."""
    from app.database import AsyncSessionLocal

    agent_loop = getattr(request.app.state, "agent_loop", None)
    if agent_loop is None:
        return

    try:
        async with AsyncSessionLocal() as db:
            for session_id in session_ids:
                msgs_result = await db.execute(
                    select(Message).where(
                        Message.session_id == session_id,
                        Message.role.in_(["user", "assistant"]),
                    )
                )
                messages = msgs_result.scalars().all()
                if not messages:
                    continue

                # Build a compact transcript (cap at 6000 chars to stay cheap)
                lines: list[str] = []
                for m in sorted(messages, key=lambda x: x.created_at or datetime.min.replace(tzinfo=UTC)):
                    role = m.role.upper()
                    snippet = (m.content or "")[:400].replace("\n", " ")
                    lines.append(f"{role}: {snippet}")
                transcript = "\n".join(lines)[:6000]

                prompt = (
                    "You are a memory distillation agent. Given a conversation transcript, "
                    "extract the most important durable facts, decisions, user preferences, "
                    "and outcomes. Write a concise structured summary (max 300 words) that "
                    "would help an assistant in a future session understand what happened "
                    "and what matters. Focus on facts, not chat pleasantries.\n\n"
                    f"TRANSCRIPT:\n{transcript}"
                )

                from app.services.llm.types import UserMessage
                result = await agent_loop.provider.chat(
                    [UserMessage(content=prompt)],
                    model="hint:fast",
                    tools=[],
                    temperature=0.3,
                )
                summary_text = ""
                from app.services.llm.types import TextContent
                for block in result.content:
                    if isinstance(block, TextContent):
                        summary_text += block.text

                if not summary_text.strip():
                    continue

                # Find or create the "Previous Sessions" root node
                root_result = await db.execute(
                    select(Memory).where(
                        Memory.title == "Previous Sessions",
                        Memory.parent_id.is_(None),
                    ).limit(1)
                )
                root = root_result.scalars().first()
                if root is None:
                    root = Memory(
                        content=(
                            "Archive of past session summaries. "
                            "Reference these when context from previous conversations may be relevant."
                        ),
                        title="Previous Sessions",
                        summary="Past session summaries — reference if needed.",
                        category="core",
                        importance=80,
                        pinned=True,
                        metadata_json={"source": "session_reset_root"},
                    )
                    db.add(root)
                    await db.flush()  # get root.id before using it as parent

                date_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
                memory = Memory(
                    content=summary_text.strip(),
                    title=f"Session summary ({date_str})",
                    summary=summary_text.strip()[:200],
                    category="core",
                    importance=65,
                    parent_id=root.id,
                    session_id=session_id,
                    metadata_json={"source": "session_reset", "user_id": user_id},
                )
                db.add(memory)

            await db.commit()
    except Exception:
        logger.warning("Memory extraction on reset failed", exc_info=True)


@router.get("/{id}")
async def get_session(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    session = await _get_owned_session(db, id, user.sub)
    return _session_response(session)


@router.delete("/{id}")
async def end_session(
    id: UUID,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    session = await _get_owned_session(db, id, user.sub)
    session.status = "ended"
    session.ended_at = datetime.now(UTC)
    await db.commit()
    return {"status": "ended"}


@router.post("/{id}/stop")
async def stop_session_generation(
    id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    _ = await _get_owned_session(db, id, user.sub)
    registry = _resolve_run_registry(request)
    cancelled = await registry.cancel(str(id))
    return {"status": "stopping" if cancelled else "idle"}


@router.post("/{id}/messages")
async def create_message(
    id: UUID,
    payload: CreateMessageRequest,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    session = await _get_owned_session(db, id, user.sub)
    if session.status != "active":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session is not active")

    message = Message(
        session_id=session.id,
        role=payload.role,
        content=payload.content,
        metadata_json=payload.metadata,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return _message_response(message)


@router.get("/{id}/messages")
async def list_messages(
    id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    before: UUID | None = Query(default=None),
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MessageListResponse:
    _ = await _get_owned_session(db, id, user.sub)

    result = await db.execute(select(Message).where(Message.session_id == id))
    messages = result.scalars().all()
    messages.sort(key=lambda m: m.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)

    if before:
        before_message = next((msg for msg in messages if msg.id == before), None)
        if before_message is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
        before_created_at = before_message.created_at
        messages = [msg for msg in messages if msg.created_at and msg.created_at < before_created_at]

    sliced = messages[: limit + 1]
    has_more = len(sliced) > limit
    items = sliced[:limit]
    return MessageListResponse(items=[_message_response(item) for item in items], has_more=has_more)


@router.post("/{id}/chat", response_model=ChatResponse)
async def chat_session(
    id: UUID,
    payload: ChatRequest,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    session = await _get_owned_session(db, id, user.sub)
    if session.status != "active":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session is not active")

    agent_loop = getattr(request.app.state, "agent_loop", None)
    if agent_loop is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No LLM provider configured")

    text = payload.content.strip()
    if not text and not payload.attachments:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="content or attachments required")

    from app.services.llm.types import ImageContent, TextContent
    if payload.attachments:
        user_blocks: list[TextContent | ImageContent] = []
        if text:
            user_blocks.append(TextContent(text=text))
        for item in payload.attachments:
            base64_data = item.base64.strip()
            if ";base64," in base64_data:
                _, _, base64_data = base64_data.partition(";base64,")
            user_blocks.append(
                ImageContent(
                    media_type=item.mime_type,
                    data=base64_data,
                )
            )
        user_payload: str | list[TextContent | ImageContent] = user_blocks
    else:
        user_payload = text

    result = await agent_loop.run(
        db,
        session.id,
        user_payload,
        system_prompt=payload.system_prompt,
        model=payload.model or "hint:reasoning",
        temperature=payload.temperature,
        max_iterations=payload.max_iterations,
        stream=False,
    )
    return ChatResponse(
        response=result.final_text,
        iterations=result.iterations,
        usage={
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
        },
        error=getattr(result, "error", None),
    )


async def _get_owned_session(db: AsyncSession, session_id: UUID, user_id: str) -> Session:
    result = await db.execute(
        select(Session).where(Session.id == session_id, Session.user_id == user_id)
    )
    session = result.scalars().first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


def _session_response(session: Session) -> SessionResponse:
    return SessionResponse(
        id=session.id,
        user_id=session.user_id,
        agent_id=session.agent_id,
        title=session.title,
        status=session.status,
        started_at=session.started_at,
        ended_at=session.ended_at,
    )


def _resolve_run_registry(request: Request) -> AgentRunRegistry:
    registry = getattr(request.app.state, "agent_run_registry", None)
    if isinstance(registry, AgentRunRegistry):
        return registry
    fallback = AgentRunRegistry()
    request.app.state.agent_run_registry = fallback
    return fallback


def _message_response(message: Message) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        content=message.content,
        metadata=message.metadata_json or {},
        token_count=message.token_count,
        tool_call_id=message.tool_call_id,
        tool_name=message.tool_name,
        created_at=message.created_at,
    )
