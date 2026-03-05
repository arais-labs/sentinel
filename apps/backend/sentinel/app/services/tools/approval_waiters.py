from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ToolApproval
from app.services.approvals.tool_match import build_tool_match_key
from app.services.tools.registry import (
    ToolApprovalOutcome,
    ToolApprovalOutcomeStatus,
    ToolApprovalRequirement,
    ToolApprovalWaiterFn,
)

_POLL_INTERVAL_SECONDS = 1.5


def build_tool_db_approval_waiter(
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> ToolApprovalWaiterFn:
    async def _waiter(
        tool_name: str,
        payload: dict[str, Any],
        requirement: ToolApprovalRequirement,
    ) -> ToolApprovalOutcome:
        session_id = _extract_session_id(payload)
        timeout_seconds = max(1, int(requirement.timeout_seconds))
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=timeout_seconds)
        requested_by = (
            requirement.requested_by.strip()
            if isinstance(requirement.requested_by, str) and requirement.requested_by.strip()
            else (f"session:{session_id}" if session_id is not None else None)
        )
        match_key = build_tool_match_key(
            tool_name=tool_name,
            payload=payload,
            explicit=requirement.match_key,
        )
        metadata = dict(requirement.metadata or {})
        metadata.setdefault("tool_name", tool_name)

        async with session_factory() as db:
            row = ToolApproval(
                provider="tool",
                tool_name=tool_name,
                session_id=session_id,
                action=requirement.action.strip(),
                description=requirement.description.strip() if requirement.description else None,
                match_key=match_key,
                status="pending",
                requested_by=requested_by,
                payload_json=metadata or None,
                expires_at=expires_at,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)

        approval_payload = _approval_payload(row)
        try:
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
                approval={**approval_payload, "status": "cancelled", "pending": False, "can_resolve": False},
                message="Approval cancelled.",
            )

        return ToolApprovalOutcome(
            status=decision.status,
            approval={
                **approval_payload,
                "status": decision.status.value,
                "pending": False,
                "can_resolve": False,
                "decision_note": decision.approval.get("decision_note"),
                "decision_by": decision.approval.get("decision_by"),
            },
            message=decision.message,
        )

    return _waiter


def _extract_session_id(payload: dict[str, Any]) -> UUID | None:
    raw = payload.get("session_id")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return UUID(raw.strip())
    except ValueError:
        return None


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
                return ToolApprovalOutcome(
                    status=ToolApprovalOutcomeStatus(status_value),
                    approval={
                        "decision_note": row.decision_note,
                        "decision_by": row.decision_by,
                    },
                    message=_status_message(status_value, row.decision_note),
                )

        if asyncio.get_running_loop().time() >= deadline:
            async with session_factory() as db:
                now = datetime.now(UTC)
                result = await db.execute(select(ToolApproval).where(ToolApproval.id == approval_id))
                row = result.scalars().first()
                if row is not None and row.status == "pending":
                    row.status = "timed_out"
                    row.decision_note = "Timed out waiting for approval"
                    row.resolved_at = now
                    await db.commit()
            return ToolApprovalOutcome(
                status=ToolApprovalOutcomeStatus.TIMED_OUT,
                approval={},
                message="Approval timed out.",
            )

        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _cancel_pending_approval(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    approval_id: UUID,
    note: str,
) -> None:
    async with session_factory() as db:
        result = await db.execute(select(ToolApproval).where(ToolApproval.id == approval_id))
        row = result.scalars().first()
        if row is None or row.status != "pending":
            return
        row.status = "cancelled"
        row.decision_note = note
        row.resolved_at = datetime.now(UTC)
        await db.commit()


def _approval_payload(row: ToolApproval) -> dict[str, Any]:
    return {
        "provider": "tool",
        "approval_id": str(row.id),
        "status": row.status,
        "pending": row.status == "pending",
        "can_resolve": row.status == "pending",
        "label": f"{row.tool_name} approval",
        "action": row.action,
        "description": row.description,
        "match_key": row.match_key,
        "session_id": str(row.session_id) if row.session_id else None,
    }


def _status_message(status: str, decision_note: str | None) -> str:
    note = (decision_note or "").strip()
    if status == "approved":
        return note or "Approval approved."
    if status == "rejected":
        return note or "Approval rejected."
    if status == "timed_out":
        return note or "Approval timed out."
    if status == "cancelled":
        return note or "Approval cancelled."
    return note or f"Approval {status}."
