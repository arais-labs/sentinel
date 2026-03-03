from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_admin
from app.models import GitAccount, GitPushApproval
from app.schemas.git import (
    CreateGitAccountRequest,
    GitAccountListResponse,
    GitAccountResponse,
    GitPushApprovalListResponse,
    GitPushApprovalResponse,
    ResolveGitPushApprovalRequest,
    UpdateGitAccountRequest,
)

router = APIRouter()


@router.get("/accounts")
async def list_git_accounts(
    _: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> GitAccountListResponse:
    result = await db.execute(select(GitAccount))
    items = result.scalars().all()
    items.sort(key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return GitAccountListResponse(items=[_account_response(item) for item in items], total=len(items))


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_git_account(
    payload: CreateGitAccountRequest,
    _: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> GitAccountResponse:
    normalized_name = payload.name.strip()
    existing = await db.execute(select(GitAccount).where(GitAccount.name == normalized_name))
    if existing.scalars().first() is not None:
        raise HTTPException(status_code=409, detail="Git account name already exists")

    account = GitAccount(
        name=normalized_name,
        host=payload.host.strip().lower(),
        scope_pattern=(payload.scope_pattern or "*").strip(),
        author_name=payload.author_name.strip(),
        author_email=payload.author_email.strip(),
        token_read=payload.token_read.strip(),
        token_write=payload.token_write.strip(),
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return _account_response(account)


@router.patch("/accounts/{account_id}")
async def update_git_account(
    account_id: UUID,
    payload: UpdateGitAccountRequest,
    _: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> GitAccountResponse:
    result = await db.execute(select(GitAccount).where(GitAccount.id == account_id))
    account = result.scalars().first()
    if account is None:
        raise HTTPException(status_code=404, detail="Git account not found")

    if payload.name is not None:
        normalized_name = payload.name.strip()
        existing = await db.execute(
            select(GitAccount).where(GitAccount.name == normalized_name, GitAccount.id != account_id)
        )
        if existing.scalars().first() is not None:
            raise HTTPException(status_code=409, detail="Git account name already exists")
        account.name = normalized_name
    if payload.host is not None:
        account.host = payload.host.strip().lower()
    if payload.scope_pattern is not None:
        account.scope_pattern = payload.scope_pattern.strip()
    if payload.author_name is not None:
        account.author_name = payload.author_name.strip()
    if payload.author_email is not None:
        account.author_email = payload.author_email.strip()
    if payload.token_read is not None:
        account.token_read = payload.token_read.strip()
    if payload.token_write is not None:
        account.token_write = payload.token_write.strip()

    await db.commit()
    await db.refresh(account)
    return _account_response(account)


@router.delete("/accounts/{account_id}")
async def delete_git_account(
    account_id: UUID,
    _: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    result = await db.execute(select(GitAccount).where(GitAccount.id == account_id))
    account = result.scalars().first()
    if account is None:
        raise HTTPException(status_code=404, detail="Git account not found")
    await db.delete(account)
    await db.commit()
    return {"success": True}


@router.get("/push-approvals")
async def list_git_push_approvals(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> GitPushApprovalListResponse:
    result = await db.execute(select(GitPushApproval))
    rows = result.scalars().all()
    if status_filter:
        rows = [row for row in rows if row.status == status_filter]
    rows.sort(key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    paged = rows[offset : offset + limit]
    return GitPushApprovalListResponse(
        items=[_push_approval_response(item) for item in paged],
        total=len(rows),
    )


@router.post("/push-approvals/{approval_id}/approve")
async def approve_git_push(
    approval_id: UUID,
    payload: ResolveGitPushApprovalRequest,
    user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> GitPushApprovalResponse:
    return await _resolve_push_approval(
        db=db,
        approval_id=approval_id,
        next_status="approved",
        decision_by=user.sub,
        decision_note=payload.note,
    )


@router.post("/push-approvals/{approval_id}/reject")
async def reject_git_push(
    approval_id: UUID,
    payload: ResolveGitPushApprovalRequest,
    user: TokenPayload = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> GitPushApprovalResponse:
    return await _resolve_push_approval(
        db=db,
        approval_id=approval_id,
        next_status="rejected",
        decision_by=user.sub,
        decision_note=payload.note,
    )


async def _resolve_push_approval(
    *,
    db: AsyncSession,
    approval_id: UUID,
    next_status: str,
    decision_by: str,
    decision_note: str | None,
) -> GitPushApprovalResponse:
    result = await db.execute(select(GitPushApproval).where(GitPushApproval.id == approval_id))
    approval = result.scalars().first()
    if approval is None:
        raise HTTPException(status_code=404, detail="Push approval not found")
    if approval.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Push approval is already resolved with status '{approval.status}'",
        )

    approval.status = next_status
    approval.decision_by = decision_by
    approval.decision_note = decision_note.strip() if isinstance(decision_note, str) else None
    approval.resolved_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(approval)
    return _push_approval_response(approval)


def _account_response(item: GitAccount) -> GitAccountResponse:
    return GitAccountResponse(
        id=item.id,
        name=item.name,
        host=item.host,
        scope_pattern=item.scope_pattern,
        author_name=item.author_name,
        author_email=item.author_email,
        has_read_token=bool((item.token_read or "").strip()),
        has_write_token=bool((item.token_write or "").strip()),
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _push_approval_response(item: GitPushApproval) -> GitPushApprovalResponse:
    return GitPushApprovalResponse(
        id=item.id,
        account_id=item.account_id,
        session_id=item.session_id,
        repo_url=item.repo_url,
        remote_name=item.remote_name,
        command=item.command,
        status=item.status,
        requested_by=item.requested_by,
        decision_by=item.decision_by,
        decision_note=item.decision_note,
        expires_at=item.expires_at,
        resolved_at=item.resolved_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
