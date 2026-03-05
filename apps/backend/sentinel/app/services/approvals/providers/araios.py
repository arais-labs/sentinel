from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.approvals.types import (
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalProviderUnavailableError,
    ApprovalRecord,
    PendingApprovalMatch,
)
from app.services.araios_client import (
    araios_request_with_auth,
    join_araios_base_and_path,
    load_araios_runtime_credentials,
)


class AraiosApprovalProvider:
    name = "araios"

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list(
        self,
        _db: AsyncSession,
        *,
        status_filter: str | None,
        limit: int,
        offset: int,
        session_id: UUID | None = None,
    ) -> tuple[list[ApprovalRecord], int]:
        base_url, agent_api_key = await self._load_credentials()
        request_kwargs: dict[str, Any] = {
            "headers": {},
            "params": {},
        }
        if status_filter:
            request_kwargs["params"]["status"] = status_filter

        url = join_araios_base_and_path(base_url, "/api/approvals")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await self._request(
                client=client,
                method="GET",
                url=url,
                request_kwargs=request_kwargs,
                base_url=base_url,
                agent_api_key=agent_api_key,
            )

        if response.status_code != 200:
            raise ApprovalProviderUnavailableError(
                f"AraiOS approvals listing failed with status {response.status_code}"
            )

        body = self._json_object(response)
        approvals_raw = body.get("approvals")
        if not isinstance(approvals_raw, list):
            approvals_raw = []

        mapped = [self._to_record(item) for item in approvals_raw if isinstance(item, dict)]
        if session_id is not None:
            wanted = str(session_id)
            mapped = [item for item in mapped if item.session_id == wanted]

        total = len(mapped)
        paged = mapped[offset : offset + limit]
        return paged, total

    async def resolve(
        self,
        _db: AsyncSession,
        *,
        approval_id: str,
        decision: str,
        decision_by: str,
        note: str | None,
    ) -> ApprovalRecord:
        base_url, agent_api_key = await self._load_credentials()
        if decision == "approve":
            action = "approve"
        elif decision == "reject":
            action = "reject"
        else:
            raise ApprovalConflictError("Unsupported approval decision")

        url = join_araios_base_and_path(base_url, f"/api/approvals/{approval_id}/{action}")
        request_kwargs: dict[str, Any] = {"headers": {}}
        if isinstance(note, str) and note.strip():
            request_kwargs["json"] = {"note": note.strip(), "resolved_by": decision_by}

        async with httpx.AsyncClient(timeout=20) as client:
            response = await self._request(
                client=client,
                method="POST",
                url=url,
                request_kwargs=request_kwargs,
                base_url=base_url,
                agent_api_key=agent_api_key,
            )

        if response.status_code == 404:
            raise ApprovalNotFoundError("AraiOS approval not found")
        if response.status_code in {409, 422}:
            raise ApprovalConflictError("AraiOS approval cannot be resolved in current state")
        if response.status_code < 200 or response.status_code >= 300:
            raise ApprovalProviderUnavailableError(
                f"AraiOS approval resolution failed with status {response.status_code}"
            )

        payload = self._json_object(response)
        return self._to_record(payload)

    def pending_match_from_tool_call(self, *, tool_name: str, arguments: dict[str, object]) -> PendingApprovalMatch | None:
        _ = tool_name, arguments
        return None

    async def _load_credentials(self) -> tuple[str, str]:
        try:
            return await load_araios_runtime_credentials(self._session_factory)
        except ValueError as exc:
            raise ApprovalProviderUnavailableError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ApprovalProviderUnavailableError(f"AraiOS credentials unavailable: {exc}") from exc

    async def _request(
        self,
        *,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        request_kwargs: dict[str, Any],
        base_url: str,
        agent_api_key: str,
    ) -> httpx.Response:
        try:
            return await araios_request_with_auth(
                client=client,
                method=method,
                url=url,
                request_kwargs=request_kwargs,
                base_url=base_url,
                agent_api_key=agent_api_key,
            )
        except ValueError as exc:
            raise ApprovalProviderUnavailableError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ApprovalProviderUnavailableError(f"AraiOS request failed: {exc}") from exc

    @staticmethod
    def _json_object(response: httpx.Response) -> dict[str, Any]:
        try:
            parsed = response.json()
        except ValueError as exc:
            raise ApprovalProviderUnavailableError("AraiOS response was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ApprovalProviderUnavailableError("AraiOS response must be an object")
        return parsed

    def _to_record(self, item: dict[str, Any]) -> ApprovalRecord:
        approval_id = str(item.get("id") or "").strip()
        status = str(item.get("status") or "pending").strip() or "pending"
        action = str(item.get("action") or "").strip() or None
        resource = str(item.get("resource") or "").strip() or None
        description = str(item.get("description") or "").strip() or None
        payload = item.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        session_id = payload.get("session_id") or payload.get("sentinel_session_id")
        if session_id is not None:
            session_id = str(session_id).strip() or None

        match_key = None
        if action:
            resource_id = item.get("resource_id")
            match_key = "|".join(
                part for part in [action.lower(), str(resource_id or "").strip().lower()] if part
            ) or None

        return ApprovalRecord(
            provider=self.name,
            approval_id=approval_id,
            status=status,
            pending=status == "pending",
            label="AraiOS approval",
            session_id=session_id,
            match_key=match_key,
            action=action,
            description=description,
            can_resolve=status == "pending",
            decision_note=str(item.get("resolved_by") or "").strip() or None,
            created_at=_parse_datetime(item.get("created_at")),
            updated_at=_parse_datetime(item.get("resolved_at")) or _parse_datetime(item.get("created_at")),
            expires_at=None,
            metadata={
                "resource": resource,
                "resource_id": item.get("resource_id"),
                "resolved_by": item.get("resolved_by"),
            },
        )


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
