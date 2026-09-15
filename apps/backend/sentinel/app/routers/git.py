from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models import GitAccount
from app.schemas.git import (
    CreateGitAccountRequest,
    GitAccountListResponse,
    GitAccountResponse,
    UpdateGitAccountRequest,
)
from app.services.secrets import is_invalid_secret
from app.services.github_account import verify_github_token
from app.routers.pull_request_review import router as review_router

router = APIRouter()

router.include_router(review_router, prefix="/reviews")


@router.get("/accounts")
async def list_git_accounts(
    db: AsyncSession = Depends(get_db),
) -> GitAccountListResponse:
    result = await db.execute(select(GitAccount))
    items = result.scalars().all()
    items.sort(key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return GitAccountListResponse(
        items=[_account_response(item) for item in items], total=len(items)
    )


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_git_account(
    payload: CreateGitAccountRequest,
    db: AsyncSession = Depends(get_db),
) -> GitAccountResponse:
    identity = await verify_github_token(payload.token)
    normalized_name = payload.name or identity.login
    existing = await db.execute(select(GitAccount).where(GitAccount.name == normalized_name))
    if existing.scalars().first() is not None:
        raise HTTPException(status_code=409, detail="Git account name already exists")

    account = GitAccount(
        name=normalized_name,
        host=payload.host.strip().lower(),
        scope_pattern=(payload.scope_pattern or "*").strip(),
        author_name=payload.author_name or identity.name,
        author_email=payload.author_email or identity.email,
        token=payload.token,
        github_login=identity.login,
        verified_at=datetime.now(UTC),
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return _account_response(account)


@router.patch("/accounts/{account_id}")
async def update_git_account(
    account_id: UUID,
    payload: UpdateGitAccountRequest,
    db: AsyncSession = Depends(get_db),
) -> GitAccountResponse:
    result = await db.execute(select(GitAccount).where(GitAccount.id == account_id))
    account = result.scalars().first()
    if account is None:
        raise HTTPException(status_code=404, detail="Git account not found")

    identity = None
    if payload.token is not None:
        if account.host != "github.com":
            raise HTTPException(422, "Only GitHub.com accounts are supported")
        identity = await verify_github_token(payload.token)
        if account.github_login and account.github_login != identity.login:
            raise HTTPException(
                422, "This token belongs to a different GitHub account. Add it as a new account."
            )

    if payload.name is not None:
        normalized_name = payload.name.strip()
        existing = await db.execute(
            select(GitAccount).where(
                GitAccount.name == normalized_name, GitAccount.id != account_id
            )
        )
        if existing.scalars().first() is not None:
            raise HTTPException(status_code=409, detail="Git account name already exists")
        account.name = normalized_name
    if payload.scope_pattern is not None:
        account.scope_pattern = payload.scope_pattern.strip()
    if payload.author_name is not None:
        account.author_name = payload.author_name.strip()
    if payload.author_email is not None:
        account.author_email = payload.author_email.strip()
    if identity is not None:
        account.token = payload.token
        account.github_login = identity.login
        account.verified_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(account)
    return _account_response(account)


@router.post("/accounts/{account_id}/check")
async def check_git_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> GitAccountResponse:
    result = await db.execute(select(GitAccount).where(GitAccount.id == account_id))
    account = result.scalars().first()
    if account is None:
        raise HTTPException(404, "Git account not found")
    if account.host != "github.com":
        raise HTTPException(422, "Only GitHub.com accounts are supported")
    if is_invalid_secret(account.token) or not account.token:
        raise HTTPException(422, "Replace this account's token before checking it")
    identity = await verify_github_token(account.token)
    if account.github_login and account.github_login != identity.login:
        raise HTTPException(422, "Token identity does not match this account. Replace the token.")
    account.github_login = identity.login
    account.verified_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(account)
    return _account_response(account)


@router.delete("/accounts/{account_id}")
async def delete_git_account(
    account_id: UUID,
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
        has_token=not is_invalid_secret(item.token) and bool((item.token or "").strip()),
        github_login=item.github_login,
        verified_at=item.verified_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
