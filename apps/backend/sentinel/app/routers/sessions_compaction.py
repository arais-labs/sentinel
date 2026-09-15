from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_request_instance_runtime_context
from app.schemas.compaction import CompactionResponse
from app.services.instance_runtime_context import InstanceRuntimeContext
from app.services.sessions.compaction import CompactionService

router = APIRouter()


@router.post("/{id:uuid}/compact", response_model=CompactionResponse)
async def compact_session(
    id: UUID,
    db: AsyncSession = Depends(get_db),
    context: InstanceRuntimeContext = Depends(get_request_instance_runtime_context),
) -> CompactionResponse:
    provider = (
        context.agent_runtime_support.provider
        if context.agent_runtime_support is not None
        else None
    )
    compaction = CompactionService(provider=provider)
    try:
        result = await compaction.compact_session(db, session_id=id, user_id="local")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return CompactionResponse(
        session_id=result.session_id,
        compacted=result.compacted,
        summary_preview=result.summary_preview,
    )
