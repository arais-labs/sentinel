"""Session-list attention state, independent of whether the agent is running."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ToolApproval


async def pending_approval_sessions(db: AsyncSession, session_ids: list[UUID]) -> set[UUID]:
    if not session_ids:
        return set()
    result = await db.execute(
        select(ToolApproval.session_id)
        .where(
            ToolApproval.session_id.in_(session_ids),
            ToolApproval.status == "pending",
            ToolApproval.expires_at > datetime.now(UTC),
        )
        .distinct()
    )
    return set(result.scalars().all())
