from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ToolApproval
from app.services.approvals.types import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalRecord,
    PendingApprovalMatch,
)


def normalize_git_command(command: str) -> str:
    return " ".join(command.strip().split()).lower()


class GitApprovalProvider:
    name = "git"

    async def list(
        self,
        db: AsyncSession,
        *,
        status_filter: str | None,
        limit: int,
        offset: int,
        session_id: UUID | None = None,
    ) -> tuple[list[ApprovalRecord], int]:
        result = await db.execute(
            select(ToolApproval).where(ToolApproval.provider == self.name)
        )
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
            raise ApprovalNotFoundError("Git approval not found") from exc

        result = await db.execute(
            select(ToolApproval).where(
                ToolApproval.id == approval_uuid,
                ToolApproval.provider == self.name,
            )
        )
        row = result.scalars().first()
        if row is None:
            raise ApprovalNotFoundError("Git approval not found")
        if row.status != "pending":
            raise ApprovalConflictError(
                f"Git approval is already resolved with status '{row.status}'"
            )

        if decision == "approve":
            next_status = "approved"
        elif decision == "reject":
            next_status = "rejected"
        else:
            raise ApprovalConflictError("Unsupported approval decision")

        row.status = next_status
        row.decision_by = decision_by
        row.decision_note = note.strip() if isinstance(note, str) else None
        row.resolved_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(row)
        return self._to_record(row)

    def pending_match_from_tool_call(self, *, tool_name: str, arguments: dict[str, object]) -> PendingApprovalMatch | None:
        if tool_name != "git_exec":
            return None
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return None
        return PendingApprovalMatch(provider=self.name, match_key=normalize_git_command(command))

    def _to_record(self, row: ToolApproval) -> ApprovalRecord:
        payload = dict(row.payload_json or {})
        command = None
        command_value = payload.get("command")
        if isinstance(command_value, str) and command_value.strip():
            command = command_value.strip()
        metadata: dict[str, object] = dict(payload)
        if row.requested_by and "requested_by" not in metadata:
            metadata["requested_by"] = row.requested_by
        if row.decision_by and "decision_by" not in metadata:
            metadata["decision_by"] = row.decision_by
        if row.tool_name and "tool_name" not in metadata:
            metadata["tool_name"] = row.tool_name
        return ApprovalRecord(
            provider=self.name,
            approval_id=str(row.id),
            status=row.status,
            pending=row.status == "pending",
            label="Git write approval",
            session_id=str(row.session_id) if row.session_id else None,
            match_key=row.match_key,
            command=command,
            action=row.action,
            description=row.description,
            can_resolve=row.status == "pending",
            decision_note=row.decision_note,
            created_at=row.created_at,
            updated_at=row.updated_at,
            expires_at=row.expires_at,
            metadata=metadata,
        )
