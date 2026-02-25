from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class CompactionResponse(BaseModel):
    session_id: UUID
    raw_token_count: int
    compressed_token_count: int
    summary_preview: str
