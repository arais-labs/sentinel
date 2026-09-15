from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class CompactionResponse(BaseModel):
    session_id: UUID
    compacted: bool
    summary_preview: str
