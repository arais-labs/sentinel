"""Validate only the credential explicitly supplied to Sentinel."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import HTTPException


@dataclass(frozen=True)
class GitHubIdentity:
    login: str
    name: str
    email: str


async def verify_github_token(token: str) -> GitHubIdentity:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
    except httpx.RequestError:
        raise HTTPException(502, "Could not reach GitHub. Try again.") from None
    if response.status_code == 401:
        raise HTTPException(
            422, "GitHub rejected this token. It may be invalid, expired or revoked."
        )
    if response.status_code == 403:
        raise HTTPException(
            422, "GitHub could not verify this token. Check its access or try again later."
        )
    if response.status_code != 200:
        raise HTTPException(502, "GitHub could not verify the account. Try again.")
    try:
        profile = response.json()
        login = profile["login"]
        user_id = profile["id"]
        if not isinstance(login, str) or not login or not isinstance(user_id, int):
            raise ValueError("Invalid GitHub identity")
        # No extra email permission or local Git configuration is needed.
        return GitHubIdentity(
            login=login,
            name=profile.get("name") or login,
            email=profile.get("email") or f"{user_id}+{login}@users.noreply.github.com",
        )
    except (ValueError, KeyError, TypeError):
        raise HTTPException(
            502, "GitHub returned an incomplete account profile. Try again."
        ) from None
