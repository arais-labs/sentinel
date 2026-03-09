from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware.auth import TokenPayload, require_admin
from app.models import GitAccount
from app.schemas.git import (
    CreateGitAccountRequest,
    GitAccountListResponse,
    GitAccountResponse,
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
