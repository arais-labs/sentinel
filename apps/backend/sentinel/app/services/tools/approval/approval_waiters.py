from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import SessionActionGrant, ToolApproval
from app.services.tools.registry import (
    ToolApprovalOutcome,
    ToolApprovalOutcomeStatus,
    ToolApprovalRequirement,
    ToolApprovalResultRecorderFn,
    ToolRuntimeContext,
    ToolApprovalWaiterFn,
)

_POLL_INTERVAL_SECONDS = 1.5


def _json_safe(value: Any) -> Any:
    """Normalize tool results for JSON storage.

    Tool results can include terminal bytes, so sanitize recursively before
    preserving approval results.
    """
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def build_tool_db_approval_waiter(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolApprovalWaiterFn:
    async def _waiter(
        tool_name: str,
        payload: dict[str, Any],
        runtime: ToolRuntimeContext,
        requirement: ToolApprovalRequirement,
        pending_callback: Any = None,
    ) -> ToolApprovalOutcome:
        session_id = runtime.session_id
        timeout_seconds = max(1, int(requirement.timeout_seconds))
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=timeout_seconds)
        requested_by = (
            requirement.requested_by.strip()
            if isinstance(requirement.requested_by, str) and requirement.requested_by.strip()
            else (f"session:{session_id}" if session_id is not None else None)
        )
        metadata = dict(requirement.metadata or {})
        metadata.setdefault("tool_name", tool_name)

        async with session_factory() as db:
            row = ToolApproval(
                provider=tool_name,
                tool_name=tool_name,
                session_id=session_id,
                action=requirement.action.strip(),
                description=(requirement.description.strip() if requirement.description else None),
                status="pending",
                requested_by=requested_by,
                payload_json=metadata or None,
                expires_at=expires_at,
            )
            db.add(row)
            # Flush first: serialize grant lookup with grants/revocations and
            # approval resolution using the instance database's writer lock.
            await db.flush()
            if session_id is not None:
                grant = await db.scalar(
                    select(SessionActionGrant).where(
                        SessionActionGrant.session_id == session_id,
                        SessionActionGrant.action == row.action,
                    )
                )
                if grant is not None:
                    row.status = "approved"
                    row.resolved_at = now
                    row.decision_by = grant.approved_by
                    row.decision_note = "Allowed for this session"
                    row.payload_json = {
                        **metadata,
                        "approval_scope": "session",
                        "session_grant_id": str(grant.id),
                    }
            await db.commit()
            await db.refresh(row)

        approval_payload = _approval_payload(row)
        if row.status == "approved":
            return _resolved_outcome(row)
        try:
            if callable(pending_callback):
                await pending_callback(approval_payload)
            decision = await _wait_for_resolution(
                session_factory=session_factory,
                approval_id=row.id,
                timeout_seconds=timeout_seconds,
            )
        except asyncio.CancelledError:
            await _cancel_pending_approval(
                session_factory=session_factory,
                approval_id=row.id,
                note="Cancelled while waiting for approval",
            )
            return ToolApprovalOutcome(
                status=ToolApprovalOutcomeStatus.CANCELLED,
                approval={
                    **approval_payload,
                    "status": "cancelled",
                    "pending": False,
                    "can_resolve": False,
                },
                message="Approval cancelled.",
            )

        return ToolApprovalOutcome(
            status=decision.status,
            approval={
                **approval_payload,
                **decision.approval,
                "status": decision.status.value,
                "pending": False,
                "can_resolve": False,
                "decision_note": decision.approval.get("decision_note"),
                "decision_by": decision.approval.get("decision_by"),
            },
            message=decision.message,
        )

    return _waiter


def build_tool_db_approval_result_recorder(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolApprovalResultRecorderFn:
    async def _record(approval_id: str, result: Any) -> None:
        try:
            approval_uuid = UUID(str(approval_id))
        except ValueError:
            return

        async with session_factory() as db:
            db_result = await db.execute(
                select(ToolApproval).where(ToolApproval.id == approval_uuid)
            )
            approval = db_result.scalars().first()
            if approval is None:
                return
            approval.result_json = _json_safe(
                result if isinstance(result, dict) else {"result": result}
            )
            await db.commit()

    return _record


async def _wait_for_resolution(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    approval_id: UUID,
    timeout_seconds: int,
) -> ToolApprovalOutcome:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        async with session_factory() as db:
            result = await db.execute(select(ToolApproval).where(ToolApproval.id == approval_id))
            row = result.scalars().first()
            if row is None:
                return ToolApprovalOutcome(
                    status=ToolApprovalOutcomeStatus.CANCELLED,
                    approval={},
                    message="Approval record was removed before completion.",
                )
            status_value = (row.status or "").strip().lower()
            if status_value in {
                ToolApprovalOutcomeStatus.APPROVED.value,
                ToolApprovalOutcomeStatus.REJECTED.value,
                ToolApprovalOutcomeStatus.TIMED_OUT.value,
                ToolApprovalOutcomeStatus.CANCELLED.value,
            }:
                return _resolved_outcome(row)

        if asyncio.get_running_loop().time() >= deadline:
            async with session_factory() as db:
                now = datetime.now(UTC)
                await db.execute(
                    update(ToolApproval)
                    .where(
                        ToolApproval.id == approval_id,
                        ToolApproval.status == "pending",
                    )
                    .values(
                        status="timed_out",
                        decision_note="Timed out waiting for approval",
                        resolved_at=now,
                    )
                )
                await db.commit()
                row = await db.get(ToolApproval, approval_id)
                if row is not None:
                    return _resolved_outcome(row)
            return ToolApprovalOutcome(
                status=ToolApprovalOutcomeStatus.CANCELLED,
                approval={},
                message="Approval removed.",
            )

        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _cancel_pending_approval(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    approval_id: UUID,
    note: str,
) -> None:
    async with session_factory() as db:
        await db.execute(
            update(ToolApproval)
            .where(
                ToolApproval.id == approval_id,
                ToolApproval.status == "pending",
            )
            .values(status="cancelled", decision_note=note, resolved_at=datetime.now(UTC))
        )
        await db.commit()


def _approval_payload(row: ToolApproval) -> dict[str, Any]:
    return {
        "provider": row.provider,
        "approval_id": str(row.id),
        "status": row.status,
        "pending": row.status == "pending",
        "can_resolve": row.status == "pending",
        "label": f"{row.tool_name} approval",
        "action": row.action,
        "description": row.description,
        "session_id": str(row.session_id) if row.session_id else None,
        "decision_by": row.decision_by,
        "decision_note": row.decision_note,
        "approval_scope": (row.payload_json or {}).get("approval_scope", "once"),
        "session_grant_id": (row.payload_json or {}).get("session_grant_id"),
    }


def _resolved_outcome(row: ToolApproval) -> ToolApprovalOutcome:
    return ToolApprovalOutcome(
        status=ToolApprovalOutcomeStatus(row.status),
        approval=_approval_payload(row),
        message=_status_message(row.status, row.decision_note),
    )


def _status_message(status: str, decision_note: str | None) -> str:
    note = (decision_note or "").strip()
    if status == "approved":
        return note or "Approval approved."
    if status == "rejected":
        return note or "User rejected action."
    if status == "timed_out":
        return note or "Approval timed out."
    if status == "cancelled":
        return note or "Approval cancelled."
    return note or f"Approval {status}."
