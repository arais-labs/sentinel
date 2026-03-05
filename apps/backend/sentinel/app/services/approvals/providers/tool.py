from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ToolApproval
from app.services.approvals.tool_match import build_tool_match_key
from app.services.approvals.types import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalRecord,
    PendingApprovalMatch,
)


class ToolApprovalProvider:
    name = "tool"

    async def list(
        self,
        db: AsyncSession,
        *,
        status_filter: str | None,
        limit: int,
        offset: int,
        session_id: UUID | None = None,
    ) -> tuple[list[ApprovalRecord], int]:
        result = await db.execute(select(ToolApproval))
        rows = result.scalars().all()
        if status_filter:
            rows = [row for row in rows if row.status == status_filter]
        if session_id is not None:
            rows = [row for row in rows if row.session_id == session_id]
        rows.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        total = len(rows)
        paged = rows[offset : offset + limit]
        return [self._to_record(row) for row in paged], total

    async def resolve(
        self,
        db: AsyncSession,
        *,
        approval_id: str,
        decision: str,
        decision_by: str,
        note: str | None,
    ) -> ApprovalRecord:
        try:
            approval_uuid = UUID(approval_id)
        except ValueError as exc:
            raise ApprovalNotFoundError("Tool approval not found") from exc

        result = await db.execute(select(ToolApproval).where(ToolApproval.id == approval_uuid))
        row = result.scalars().first()
        if row is None:
            raise ApprovalNotFoundError("Tool approval not found")
        if row.status != "pending":
            raise ApprovalConflictError(
                f"Tool approval is already resolved with status '{row.status}'"
            )

        if decision == "approve":
            next_status = "approved"
        elif decision == "reject":
            next_status = "rejected"
        else:
            raise ApprovalConflictError("Unsupported approval decision")

        row.status = next_status
        row.decision_by = decision_by
        row.decision_note = note.strip() if isinstance(note, str) and note.strip() else None
        row.resolved_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        return self._to_record(row)

    def pending_match_from_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
    ) -> PendingApprovalMatch | None:
        match_key = build_tool_match_key(tool_name=tool_name, payload=arguments)
        return PendingApprovalMatch(provider=self.name, match_key=match_key)

    def _to_record(self, row: ToolApproval) -> ApprovalRecord:
        metadata = dict(row.payload_json or {})
        metadata.setdefault("tool_name", row.tool_name)
        return ApprovalRecord(
            provider=self.name,
            approval_id=str(row.id),
            status=row.status,
            pending=row.status == "pending",
            label=f"{row.tool_name} approval",
            session_id=str(row.session_id) if row.session_id else None,
            match_key=row.match_key,
            action=row.action,
            description=row.description,
            can_resolve=row.status == "pending",
            decision_note=row.decision_note,
            created_at=row.created_at,
            updated_at=row.updated_at,
            expires_at=row.expires_at,
            metadata=metadata,
        )
