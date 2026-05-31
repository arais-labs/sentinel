from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_request_instance_runtime_context
from app.middleware.auth import TokenPayload, require_auth
from app.schemas.compaction import CompactionResponse
from app.services.instance_runtime_context import InstanceRuntimeContext
from app.services.sessions.compaction import CompactionService

router = APIRouter()


@router.post("/{id}/compact", response_model=CompactionResponse)
async def compact_session(
    id: UUID,
    db: AsyncSession = Depends(get_db),
    context: InstanceRuntimeContext = Depends(get_request_instance_runtime_context),
    user: TokenPayload = Depends(require_auth),
) -> CompactionResponse:
    provider = (
        context.agent_runtime_support.provider
        if context.agent_runtime_support is not None
        else None
    )
    compaction = CompactionService(provider=provider)
    result = await compaction.compact_session(db, session_id=id, user_id=user.sub)
    return CompactionResponse(
        session_id=result.session_id,
        raw_token_count=result.raw_token_count,
        compressed_token_count=result.compressed_token_count,
        summary_preview=result.summary_preview,
    )
