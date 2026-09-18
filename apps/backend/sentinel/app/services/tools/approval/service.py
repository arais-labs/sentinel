from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ToolApproval
from app.services.tools.approval.providers.tool import ToolApprovalProvider
from app.services.tools.approval.types import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalProviderUnavailableError,
    ApprovalRecord,
)


class ApprovalService:
    # DB sessions are passed per-call so each invocation hits the right
    # per-instance database. There is no service-level session factory.
    def __init__(self) -> None:
        self._tool_provider = ToolApprovalProvider()

    async def cancel_pending_on_startup(self, db: AsyncSession) -> None:
        """Previous-process approval waiters cannot survive a backend restart."""
        await db.execute(
            update(ToolApproval)
            .where(ToolApproval.status == "pending")
            .values(
                status="cancelled",
                decision_note="Request interrupted. Please try again.",
                resolved_at=datetime.now(UTC),
            )
        )
        await db.commit()

    async def list_approvals(
        self,
        db: AsyncSession,
        *,
        status_filter: str | None,
        limit: int,
        offset: int,
        provider: str | None = None,
        session_id: UUID | None = None,
    ) -> tuple[list[ApprovalRecord], int]:
        provider_rows, _ = await self._tool_provider.list(
            db,
            provider=(provider.strip() if isinstance(provider, str) and provider.strip() else None),
            status_filter=status_filter,
            limit=max(500, limit + offset),
            offset=0,
            session_id=session_id,
        )
        records = list(provider_rows)
        records.sort(
            key=lambda item: item.created_at.timestamp() if item.created_at else 0,
            reverse=True,
        )
        total = len(records)
        paged = records[offset : offset + limit]
        return paged, total

    async def resolve_approval(
        self,
        db: AsyncSession,
        *,
        provider: str,
        approval_id: str,
        decision: str,
        decision_by: str,
        note: str | None,
        scope: str = "once",
    ) -> ApprovalRecord:
        normalized = provider.strip()
        if not normalized:
            raise ApprovalProviderUnavailableError("Approval provider is required")
        return await self._tool_provider.resolve(
            db,
            provider=normalized,
            approval_id=approval_id,
            decision=decision,
            decision_by=decision_by,
            note=note,
            scope=scope,
        )


__all__ = [
    "ApprovalConflictError",
    "ApprovalNotFoundError",
    "ApprovalProviderUnavailableError",
    "ApprovalService",
]
