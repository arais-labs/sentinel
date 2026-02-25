from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_auth
from app.schemas.compaction import CompactionResponse
from app.services.compaction import CompactionService

router = APIRouter()


@router.post("/{id}/compact", response_model=CompactionResponse)
async def compact_session(
    id: UUID,
    request: Request,
    user: TokenPayload = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> CompactionResponse:
    provider = getattr(request.app.state, "llm_provider", None)
    compaction = CompactionService(provider=provider)
    result = await compaction.compact_session(db, session_id=id, user_id=user.sub)
    return CompactionResponse(
        session_id=result.session_id,
        raw_token_count=result.raw_token_count,
        compressed_token_count=result.compressed_token_count,
        summary_preview=result.summary_preview,
    )
