from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SessionActionGrant, ToolApproval
from app.services.tools.approval.types import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalRecord,
)


class ToolApprovalProvider:
    async def list(
        self,
        db: AsyncSession,
        *,
        provider: str | None,
        status_filter: str | None,
        limit: int,
        offset: int,
        session_id: UUID | None = None,
    ) -> tuple[list[ApprovalRecord], int]:
        stmt = select(ToolApproval)
        if isinstance(provider, str) and provider.strip():
            stmt = stmt.where(ToolApproval.provider == provider.strip())
        result = await db.execute(stmt)
        rows = result.scalars().all()
        if status_filter:
            rows = [row for row in rows if row.status == status_filter]
        if session_id is not None:
            rows = [row for row in rows if row.session_id == session_id]
        rows.sort(
            key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        total = len(rows)
        paged = rows[offset : offset + limit]
        return [self._to_record(row) for row in paged], total

    async def resolve(
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
        try:
            approval_uuid = UUID(approval_id)
        except ValueError as exc:
            raise ApprovalNotFoundError("Tool approval not found") from exc

        if scope not in {"once", "session"} or (scope == "session" and decision != "approve"):
            raise ApprovalConflictError("Session permission can only be granted when approving")
        if decision not in {"approve", "reject"}:
            raise ApprovalConflictError("Unsupported approval decision")
        now = datetime.now(UTC)
        # Compare-and-set acquires the SQLite writer lock before reading the
        # grant. Resolution, cancellation, expiry and new requests serialize.
        result = await db.execute(
            update(ToolApproval)
            .where(
                ToolApproval.id == approval_uuid,
                ToolApproval.provider == provider,
                ToolApproval.status == "pending",
                ToolApproval.expires_at > now,
            )
            .values(
                status="approved" if decision == "approve" else "rejected",
                decision_by=decision_by,
                decision_note=(note.strip() if isinstance(note, str) and note.strip() else None),
                resolved_at=now,
            )
            .returning(ToolApproval)
        )
        row = result.scalars().first()
        if row is None:
            existing = await db.scalar(
                select(ToolApproval).where(
                    ToolApproval.id == approval_uuid,
                    ToolApproval.provider == provider,
                )
            )
            status = existing.status if existing is not None else None
            await db.rollback()
            if existing is None:
                raise ApprovalNotFoundError("Tool approval not found")
            raise ApprovalConflictError(
                "Tool approval has expired"
                if status == "pending"
                else f"Tool approval is already resolved with status '{status}'"
            )
        if scope == "session":
            if row.session_id is None or not row.action:
                await db.rollback()
                raise ApprovalConflictError("This approval has no session action to remember")
            await db.execute(
                insert(SessionActionGrant)
                .values(
                    session_id=row.session_id,
                    action=row.action,
                    approved_by=decision_by,
                )
                .on_conflict_do_nothing(index_elements=["session_id", "action"])
            )
            grant = await db.scalar(
                select(SessionActionGrant).where(
                    SessionActionGrant.session_id == row.session_id,
                    SessionActionGrant.action == row.action,
                )
            )
            # Match the canonical module action, including calls through grouped
            # or individual tool aliases. Never widen to the whole provider.
            matching = (
                await db.scalars(
                    select(ToolApproval).where(
                        ToolApproval.session_id == row.session_id,
                        ToolApproval.action == row.action,
                        ToolApproval.status == "pending",
                        ToolApproval.expires_at > now,
                    )
                )
            ).all()
            for approved in [row, *matching]:
                approved.status = "approved"
                approved.resolved_at = now
                approved.decision_by = decision_by
                approved.decision_note = "Allowed for this session"
                approved.payload_json = {
                    **(approved.payload_json or {}),
                    "approval_scope": "session",
                    "session_grant_id": str(grant.id),
                }
        await db.commit()
        await db.refresh(row)
        return self._to_record(row)

    def _to_record(self, row: ToolApproval) -> ApprovalRecord:
        metadata = dict(row.payload_json or {})
        metadata.setdefault("tool_name", row.tool_name)
        return ApprovalRecord(
            provider=row.provider,
            approval_id=str(row.id),
            status=row.status,
            pending=row.status == "pending",
            label=f"{row.tool_name} approval",
            session_id=str(row.session_id) if row.session_id else None,
            action=row.action,
            description=row.description,
            can_resolve=row.status == "pending",
            decision_note=row.decision_note,
            created_at=row.created_at,
            updated_at=row.updated_at,
            expires_at=row.expires_at,
            metadata=metadata,
        )
