from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.approvals.providers.araios import AraiosApprovalProvider
from app.services.approvals.providers.git import GitApprovalProvider
from app.services.approvals.providers.tool import ToolApprovalProvider
from app.services.approvals.types import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalProviderUnavailableError,
    ApprovalRecord,
    PendingApprovalMatch,
)

logger = logging.getLogger(__name__)


class _ApprovalProvider(Protocol):
    name: str

    async def list(
        self,
        db: AsyncSession,
        *,
        status_filter: str | None,
        limit: int,
        offset: int,
        session_id: UUID | None = None,
    ) -> tuple[list[ApprovalRecord], int]: ...

    async def resolve(
        self,
        db: AsyncSession,
        *,
        approval_id: str,
        decision: str,
        decision_by: str,
        note: str | None,
    ) -> ApprovalRecord: ...

    def pending_match_from_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
    ) -> PendingApprovalMatch | None: ...


class ApprovalService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._providers: dict[str, _ApprovalProvider] = {
            "tool": ToolApprovalProvider(),
            "git": GitApprovalProvider(),
            "araios": AraiosApprovalProvider(session_factory=session_factory),
        }

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
        selected = self._select_providers(provider)
        if provider and not selected:
            raise ApprovalProviderUnavailableError(f"Unsupported approval provider '{provider}'")

        records: list[ApprovalRecord] = []
        for current in selected:
            try:
                provider_rows, _ = await current.list(
                    db,
                    status_filter=status_filter,
                    limit=max(500, limit + offset),
                    offset=0,
                    session_id=session_id,
                )
            except ApprovalProviderUnavailableError:
                if provider:
                    raise
                continue
            records.extend(provider_rows)

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
    ) -> ApprovalRecord:
        selected = self._providers.get(provider.strip().lower())
        if selected is None:
            raise ApprovalProviderUnavailableError(f"Unsupported approval provider '{provider}'")
        return await selected.resolve(
            db,
            approval_id=approval_id,
            decision=decision,
            decision_by=decision_by,
            note=note,
        )

    async def match_pending_for_unresolved_calls(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        unresolved_calls: list[dict[str, object]],
    ) -> dict[str, ApprovalRecord]:
        if not unresolved_calls:
            return {}
        logger.info(
            "approval_match_start session_id=%s unresolved_count=%s",
            session_id,
            len(unresolved_calls),
        )

        provider_keys: dict[str, list[tuple[str, str]]] = {}
        call_debug: dict[str, dict[str, object]] = {}
        for call in unresolved_calls:
            call_id = str(call.get("id") or "").strip()
            if not call_id:
                continue
            call_debug[call_id] = {
                "tool_name": str(call.get("name") or "").strip() or None,
                "has_hint": isinstance(call.get("approval_hint"), dict),
            }
            approval_hint = call.get("approval_hint")
            if isinstance(approval_hint, dict):
                provider_name = str(approval_hint.get("provider") or "").strip().lower()
                match_key = str(approval_hint.get("match_key") or "").strip()
                if provider_name in self._providers and match_key:
                    provider_keys.setdefault(provider_name, []).append((call_id, match_key))
                    continue

            tool_name = str(call.get("name") or "").strip()
            arguments = call.get("arguments")
            if not isinstance(arguments, dict):
                continue
            for provider in self._providers.values():
                pending = provider.pending_match_from_tool_call(tool_name=tool_name, arguments=arguments)
                if pending is None:
                    continue
                provider_keys.setdefault(provider.name, []).append((call_id, pending.match_key))

        if not provider_keys:
            logger.info("approval_match_no_candidates session_id=%s", session_id)
            return {}

        pending_by_provider: dict[str, dict[str, list[ApprovalRecord]]] = {}
        for provider_name, matches in provider_keys.items():
            provider = self._providers.get(provider_name)
            if provider is None:
                continue
            try:
                rows, _ = await provider.list(
                    db,
                    status_filter="pending",
                    limit=max(200, len(matches) * 3),
                    offset=0,
                    session_id=session_id,
                )
            except ApprovalProviderUnavailableError:
                continue

            buckets: dict[str, list[ApprovalRecord]] = {}
            for row in rows:
                if not row.match_key:
                    continue
                buckets.setdefault(row.match_key, []).append(row)
            for items in buckets.values():
                items.sort(
                    key=lambda item: item.created_at.timestamp() if item.created_at else 0,
                )
            pending_by_provider[provider_name] = buckets
            logger.info(
                "approval_match_provider_pending session_id=%s provider=%s requested_matches=%s pending_rows=%s",
                session_id,
                provider_name,
                len(matches),
                sum(len(items) for items in buckets.values()),
            )

        resolved: dict[str, ApprovalRecord] = {}
        for provider_name, matches in provider_keys.items():
            buckets = pending_by_provider.get(provider_name, {})
            for call_id, match_key in matches:
                items = buckets.get(match_key, [])
                if not items:
                    continue
                resolved[call_id] = items.pop(0)
        expected_call_ids = {
            call_id
            for matches in provider_keys.values()
            for call_id, _ in matches
        }
        missing_call_ids = sorted(expected_call_ids - set(resolved.keys()))
        for call_id in missing_call_ids:
            debug = call_debug.get(call_id, {})
            logger.warning(
                "approval_match_missing session_id=%s tool_call_id=%s tool_name=%s has_hint=%s",
                session_id,
                call_id,
                debug.get("tool_name"),
                debug.get("has_hint"),
            )
        logger.info(
            "approval_match_done session_id=%s resolved_count=%s",
            session_id,
            len(resolved),
        )
        return resolved

    def _select_providers(self, provider: str | None) -> list[_ApprovalProvider]:
        if provider is None:
            return list(self._providers.values())
        selected = self._providers.get(provider.strip().lower())
        return [selected] if selected is not None else []


__all__ = [
    "ApprovalConflictError",
    "ApprovalNotFoundError",
    "ApprovalProviderUnavailableError",
    "ApprovalService",
]
