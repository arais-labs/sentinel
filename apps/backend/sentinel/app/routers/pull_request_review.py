from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ConfigDict, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.services import pull_request_review as service

router = APIRouter()


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=1000)
    account_id: UUID | None = None


class Comment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096)
    line: int = Field(gt=0)
    side: Literal["LEFT", "RIGHT"]
    body: str = Field(min_length=1, max_length=60000)


class Review(Target):
    commit_id: str = Field(pattern=r"^[a-f0-9]{40}$")
    event: Literal["COMMENT", "APPROVE", "REQUEST_CHANGES"]
    body: str = Field(default="", max_length=60000)
    comments: list[Comment] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_body(self):
        if self.event == "REQUEST_CHANGES" and not self.body.strip():
            raise ValueError("Explain the requested changes")
        if self.event == "COMMENT" and not self.body.strip() and not self.comments:
            raise ValueError("Write a review or add a line comment")
        return self


@router.post("/load")
async def load(payload: Target, db: AsyncSession = Depends(get_db)):
    owner, repo, number = service.parse_pr(payload.url)
    account = await service.choose_account(db, payload.account_id, owner, repo)
    # Release the read transaction before streaming network requests.
    await db.commit()
    return StreamingResponse(
        service.load_review(account, owner, repo, number),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/submit")
async def submit(payload: Review, db: AsyncSession = Depends(get_db)):
    owner, repo, number = service.parse_pr(payload.url)
    account = await service.choose_account(db, payload.account_id, owner, repo)
    await db.commit()
    return StreamingResponse(
        service.submit_review(account, owner, repo, number, payload),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/inbox")
async def inbox(
    account_id: UUID | None = None,
    view: Literal["requested", "created"] = "requested",
    db: AsyncSession = Depends(get_db),
):
    account = await service.choose_account(db, account_id)
    await db.commit()
    async with service.client(account) as remote:
        user = await service.request(remote, "/user")
        query = f'is:pr is:open {"review-requested" if view == "requested" else "author"}:{user["login"]}'
        data = await service.request(
            remote, "/search/issues", params={"q": query, "sort": "updated", "per_page": 100}
        )
        items = []
        for item in data["items"]:
            owner, repo, _ = service.parse_pr(item["html_url"])
            if service.in_scope(account, owner, repo):
                items.append(
                    {
                        key: item.get(key)
                        for key in ("number", "title", "html_url", "user", "updated_at", "draft")
                    }
                )
        return {"items": items, "limited": data["total_count"] > 100}


class CheckTarget(Target):
    check_id: int = Field(gt=0)


@router.post("/checks")
async def checks(payload: CheckTarget, db: AsyncSession = Depends(get_db)):
    owner, repo, number = service.parse_pr(payload.url)
    account = await service.choose_account(db, payload.account_id, owner, repo)
    await db.commit()
    return StreamingResponse(
        service.check_progress(account, owner, repo, number, payload.check_id),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
