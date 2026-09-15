from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import GitAccount
from app.routers import git
from app.schemas.git import CreateGitAccountRequest, UpdateGitAccountRequest
from app.services.github_account import GitHubIdentity, verify_github_token
from app.services.secrets import is_invalid_secret
from app.services.secrets.types import InvalidSecretValue
from tests.fake_db import FakeDB


def mock_github(monkeypatch, status, profile):
    real_client = httpx.AsyncClient

    def respond(request):
        assert str(request.url) == "https://api.github.com/user"
        assert request.headers["authorization"] == "Bearer supplied-token"
        return httpx.Response(status, json=profile)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)),
    )


@pytest.mark.asyncio
async def test_identity_check_uses_supplied_token_and_noreply_default(monkeypatch):
    mock_github(monkeypatch, 200, {"id": 42, "login": "tester", "name": None, "email": None})
    identity = await verify_github_token("supplied-token")
    assert identity == GitHubIdentity("tester", "tester", "42+tester@users.noreply.github.com")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,profile,expected",
    [
        (401, {"message": "secret upstream message"}, 422),
        (403, {}, 422),
        (429, {}, 502),
        (500, {}, 502),
        (200, {"id": 42}, 502),
    ],
)
async def test_identity_errors_are_safe(monkeypatch, status, profile, expected):
    mock_github(monkeypatch, status, profile)
    with pytest.raises(HTTPException) as exc:
        await verify_github_token("supplied-token")
    assert exc.value.status_code == expected
    assert "secret" not in exc.value.detail
    assert "supplied-token" not in exc.value.detail


@pytest.mark.asyncio
async def test_create_defaults_and_failed_replacement_leave_account_intact(monkeypatch):
    async def verify(token):
        if token == "invalid":
            raise HTTPException(422, "Invalid token")
        return GitHubIdentity(
            "tester" if token == "original" else "someone-else", "Tester", "tester@example.com"
        )

    monkeypatch.setattr(git, "verify_github_token", verify)
    db = FakeDB()
    response = await git.create_git_account(CreateGitAccountRequest(token="original"), db)
    assert response.name == "tester"
    assert response.author_email == "tester@example.com"
    assert response.verified_at is not None
    assert response.scope_pattern == "*"
    account = db.storage[GitAccount][0]
    for token in ("invalid", "different-account"):
        with pytest.raises(HTTPException):
            await git.update_git_account(
                account.id, UpdateGitAccountRequest(token=token, name="changed"), db
            )
        assert account.token == "original"
        assert account.name == "tester"
    with pytest.raises(HTTPException):
        await git.create_git_account(CreateGitAccountRequest(token="invalid"), db)
    assert len(db.storage[GitAccount]) == 1


@pytest.mark.asyncio
async def test_unreadable_account_is_not_deleted(monkeypatch):
    db = FakeDB()
    account = GitAccount(
        name="old",
        host="github.com",
        scope_pattern="*",
        author_name="Old",
        author_email="old@example.com",
        token=InvalidSecretValue("cannot decrypt"),
    )
    db.add(account)
    response = await git.list_git_accounts(db)
    assert response.total == 1
    assert response.items[0].has_token is False
    assert len(db.storage[GitAccount]) == 1
    assert is_invalid_secret(account.token)

    async def verify(_token):
        return GitHubIdentity("old", "Old", "old@example.com")

    monkeypatch.setattr(git, "verify_github_token", verify)
    await git.update_git_account(account.id, UpdateGitAccountRequest(token="replacement"), db)
    assert account.token == "replacement"
    assert account.github_login == "old"


def test_fresh_git_account_encrypts_token(tmp_path):

    path = tmp_path / "instance.sqlite3"
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.instance.ini"))
    config.set_main_option("script_location", str(root / "db/alembic/instance"))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with Session(engine) as db:
            db.add(
                GitAccount(
                    name="test",
                    host="github.com",
                    scope_pattern="*",
                    author_name="Test",
                    author_email="test@example.com",
                    token="new-token",
                )
            )
            db.commit()
            assert (
                db.execute(text("SELECT token FROM git_accounts"))
                .scalar_one()
                .startswith("sentinel:v1:")
            )
            assert db.query(GitAccount).one().token == "new-token"
    finally:
        engine.dispose()
