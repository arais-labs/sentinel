"""AraiOS approval provider — direct DB access (no HTTP self-calls)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.araios import AraiosApproval, AraiosModule, AraiosModuleSecret
from app.services.approvals.types import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalProviderUnavailableError,
    ApprovalRecord,
    PendingApprovalMatch,
)


class AraiosApprovalProvider:
    name = "araios"

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list(
        self,
        db: AsyncSession,
        *,
        status_filter: str | None,
        limit: int,
        offset: int,
        session_id: UUID | None = None,
    ) -> tuple[list[ApprovalRecord], int]:
        stmt = select(AraiosApproval).order_by(AraiosApproval.created_at.desc())
        if status_filter:
            stmt = stmt.where(AraiosApproval.status == status_filter)
        result = await db.execute(stmt)
        rows = result.scalars().all()

        mapped = [self._to_record(row) for row in rows]
        if session_id is not None:
            wanted = str(session_id)
            mapped = [item for item in mapped if item.session_id == wanted]

        total = len(mapped)
        paged = mapped[offset: offset + limit]
        return paged, total

    async def resolve(
        self,
        db: AsyncSession,
        *,
        approval_id: str,
        decision: str,
        decision_by: str,
        note: str | None,
    ) -> ApprovalRecord:
        result = await db.execute(
            select(AraiosApproval).where(AraiosApproval.id == approval_id)
        )
        approval = result.scalars().first()
        if not approval:
            raise ApprovalNotFoundError(f"AraiOS approval '{approval_id}' not found")
        if approval.status != "pending":
            raise ApprovalConflictError(
                f"AraiOS approval is already {approval.status}"
            )

        # Execute the approved action
        if decision == "approve":
            await self._execute_approval(db, approval)
            approval.status = "approved"
        elif decision == "reject":
            approval.status = "rejected"
        else:
            raise ApprovalConflictError("Unsupported approval decision")

        approval.resolved_at = datetime.now(UTC)
        approval.resolved_by = decision_by
        await db.commit()
        await db.refresh(approval)
        return self._to_record(approval)

    def pending_match_from_tool_call(
        self, *, tool_name: str, arguments: dict[str, object]
    ) -> PendingApprovalMatch | None:
        return None

    # ── Internal ──

    async def _execute_approval(
        self, db: AsyncSession, approval: AraiosApproval
    ) -> None:
        """Execute the stored action when an approval is approved."""
        action = approval.action or ""
        payload = approval.payload or {}
        resource = approval.resource
        resource_id = approval.resource_id

        parts = action.split(".")
        action_verb = parts[-1] if len(parts) >= 2 else action

        # Module tool action
        if len(parts) == 2:
            mod_result = await db.execute(
                select(AraiosModule).where(AraiosModule.name == parts[0])
            )
            mod = mod_result.scalars().first()
            if mod and mod.type == "tool":
                action_def = next(
                    (a for a in (mod.actions or []) if a.get("id") == parts[1]),
                    None,
                )
                if action_def and action_def.get("code"):
                    from app.services.araios.executor import execute_action

                    secrets: dict[str, str] = {}
                    sec_result = await db.execute(
                        select(AraiosModuleSecret).where(
                            AraiosModuleSecret.module_name == mod.name
                        )
                    )
                    for s in sec_result.scalars().all():
                        secrets[s.key] = s.value
                    params = payload if isinstance(payload, dict) else {}
                    exec_result = await execute_action(
                        action_def["code"], {"params": params, "secrets": secrets}
                    )
                    if not exec_result.get("ok", True):
                        raise ApprovalConflictError(
                            exec_result.get("error", "Action failed")
                        )
                    return

        # Module engine create/update/delete
        if resource == "modules":
            module_name = (resource_id or payload.get("name", "")).strip().lower()
            if action_verb == "create" and module_name:
                existing = await db.execute(
                    select(AraiosModule).where(AraiosModule.name == module_name)
                )
                if existing.scalars().first():
                    raise ApprovalConflictError(
                        f"Module '{module_name}' already exists"
                    )
                mod = AraiosModule(
                    name=module_name,
                    label=payload.get("label", module_name.title()),
                    description=payload.get("description", ""),
                    icon=payload.get("icon", "box"),
                    type=payload.get("type", "data"),
                    fields=payload.get("fields", []),
                    list_config=payload.get("list_config", {}),
                    actions=payload.get("actions", []),
                    secrets=payload.get("secrets", []),
                    is_system=False,
                    order=payload.get("order", 100),
                )
                db.add(mod)
                await db.commit()
                return
            if action_verb == "delete" and module_name:
                del_result = await db.execute(
                    select(AraiosModule).where(AraiosModule.name == module_name)
                )
                mod = del_result.scalars().first()
                if mod:
                    await db.delete(mod)
                    await db.commit()
                return

    def _to_record(self, item: AraiosApproval | Any) -> ApprovalRecord:
        if isinstance(item, AraiosApproval):
            approval_id = item.id or ""
            status = item.status or "pending"
            action = item.action
            resource = item.resource
            description = item.description
            resource_id = item.resource_id
            payload = item.payload or {}
            created_at = item.created_at
            resolved_at = item.resolved_at
            resolved_by = item.resolved_by
        else:
            approval_id = str(item.get("id", ""))
            status = str(item.get("status", "pending"))
            action = item.get("action")
            resource = item.get("resource")
            description = item.get("description")
            resource_id = item.get("resource_id")
            payload = item.get("payload") or {}
            created_at = _parse_datetime(item.get("created_at"))
            resolved_at = _parse_datetime(item.get("resolved_at"))
            resolved_by = item.get("resolved_by")

        session_id = None
        if isinstance(payload, dict):
            sid = payload.get("session_id") or payload.get("sentinel_session_id")
            if sid:
                session_id = str(sid).strip() or None

        match_key = None
        if action:
            match_key = "|".join(
                part
                for part in [
                    action.lower(),
                    str(resource_id or "").strip().lower(),
                ]
                if part
            ) or None

        return ApprovalRecord(
            provider=self.name,
            approval_id=approval_id,
            status=status,
            pending=status == "pending",
            label=description or f"AraiOS: {action}" if action else "AraiOS approval",
            session_id=session_id,
            match_key=match_key,
            action=action,
            description=description,
            can_resolve=status == "pending",
            decision_note=str(resolved_by or "").strip() or None,
            created_at=created_at,
            updated_at=resolved_at or created_at,
            expires_at=None,
            metadata={
                "resource": resource,
                "resource_id": resource_id,
                "resolved_by": resolved_by,
            },
        )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        if isinstance(value, datetime):
            return value
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
